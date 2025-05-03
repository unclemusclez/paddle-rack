import yaml
import os
import subprocess
import argparse
import glob
from pathlib import Path
import paramiko

MODELS_DIR = "/etc/paddler/models"
PADDLER_USER = "paddler"

class PaddlerDeployer:
    def __init__(self, models_dir=MODELS_DIR):
        self.models_dir = models_dir
        self.configs = self.load_configs()

    def load_configs(self):
        configs = []
        for config_file in glob.glob(f"{self.models_dir}/*.yaml"):
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)
                if config['model']['name'] != Path(config_file).stem:
                    raise ValueError(f"Model name {config['model']['name']} does not match filename {Path(config_file).stem}")
                configs.append(config)
        return configs

    def validate_config(self, config):
        model_name = config['model']['name']
        config_path = f"{self.models_dir}/{model_name}.yaml"
        if not os.path.exists(config_path):
            raise ValueError(f"Config {config_path} not found")
        if not os.path.exists(config['balancer']['paddler_binary']):
            raise ValueError(f"Paddler binary {config['balancer']['paddler_binary']} not found")
        if not os.path.exists(config['model']['llama_cpp_binary']):
            raise ValueError(f"llama.cpp binary {config['model']['llama_cpp_binary']} not found")
        # Check for port conflicts (basic)
        for other_config in self.configs:
            if other_config['model']['name'] != model_name:
                if other_config['balancer']['management_addr'] == config['balancer']['management_addr']:
                    raise ValueError(f"Port conflict: {config['balancer']['management_addr']} used by {other_config['model']['name']}")
        return True

    def manage_service(self, service_name, action):
        try:
            subprocess.run(["systemctl", action, service_name], check=True)
            print(f"{action.capitalize()} {service_name} successful")
        except subprocess.CalledProcessError as e:
            print(f"Error {action}ing {service_name}: {e}")

    def deploy_remote_agent(self, config, agent):
        if agent['host'] == "localhost":
            return
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(agent['host'], username="paddler", key_filename="/home/paddler/.ssh/id_rsa")
            sftp = ssh.open_sftp()
            sftp.put(config['balancer']['paddler_binary'], "/usr/bin/paddler")
            sftp.close()
            model_name = config['model']['name']
            agent_name = agent['name']
            service_content = f"""[Unit]
Description=Paddler agent for {model_name}-{agent_name}
After=network.target

[Service]
ExecStart=/usr/bin/paddler agent \
  --external-llamacpp-addr {agent['external_llamacpp_addr']} \
  --local-llamacpp-addr {agent['local_llamacpp_addr']} \
  --management-addr {agent['management_addr']} \
  --name {agent_name} \
  {f"--local-llamacpp-api-key {agent['api_key']}" if agent.get('api_key') else ""}
Restart=always
User=paddler
Group=paddler

[Install]
WantedBy=multi-user.target
"""
            ssh.exec_command(f"echo '{service_content}' | sudo tee /etc/systemd/system/paddler-agent-{model_name}-{agent_name}.service")
            ssh.exec_command("sudo systemctl daemon-reload")
            ssh.exec_command(f"sudo systemctl enable paddler-agent-{model_name}-{agent_name}")
            ssh.exec_command(f"sudo systemctl start paddler-agent-{model_name}-{agent_name}")
            print(f"Deployed agent {agent_name} on {agent['host']}")
        except Exception as e:
            print(f"Failed to deploy agent {agent_name} on {agent['host']}: {e}")
        finally:
            ssh.close()

    def deploy_instance(self, config):
        model_name = config['model']['name']
        self.validate_config(config)
        self.manage_service(f"llama-cpp@{model_name}", "enable")
        self.manage_service(f"llama-cpp@{model_name}", "start")
        self.manage_service(f"paddler@{model_name}", "enable")
        self.manage_service(f"paddler@{model_name}", "start")
        for agent in config['agents']:
            if agent['host'] == "localhost":
                self.manage_service(f"paddler-agent@{model_name}-{agent['name']}", "enable")
                self.manage_service(f"paddler-agent@{model_name}-{agent['name']}", "start")
            else:
                self.deploy_remote_agent(config, agent)
        self.generate_inventory()

    def remove_instance(self, config):
        model_name = config['model']['name']
        for agent in config['agents']:
            if agent['host'] == "localhost":
                self.manage_service(f"paddler-agent@{model_name}-{agent['name']}", "stop")
                self.manage_service(f"paddler-agent@{model_name}-{agent['name']}", "disable")
            # Note: Remote agents require manual cleanup or SSH
        self.manage_service(f"paddler@{model_name}", "stop")
        self.manage_service(f"paddler@{model_name}", "disable")
        self.manage_service(f"llama-cpp@{model_name}", "stop")
        self.manage_service(f"llama-cpp@{model_name}", "disable")
        self.generate_inventory()

    def generate_inventory(self, inventory_file="/etc/paddler/inventory.yaml"):
        inventory = {"paddler_instances": []}
        for config in self.configs:
            model_name = config['model']['name']
            instance = {
                "name": model_name,
                "model": f"llama-{config['model']['description'].split()[0].lower()}",
                "statsd_prefix": config['balancer']['statsd_prefix'],
                "statsd_addr": config['balancer']['statsd_addr'],
                "management_addr": config['balancer']['management_addr'],
                "api_endpoint": f"http://{config['balancer']['management_addr']}/api/v1/agents",
                "reverseproxy_addr": config['balancer']['reverseproxy_addr'],
                "dashboard_url": f"http://{config['balancer']['management_addr']}/dashboard",
            }
            inventory["paddler_instances"].append(instance)
        with open(inventory_file, 'w') as f:
            yaml.dump(inventory, f)
        print(f"Generated {inventory_file}")

    def run(self):
        parser = argparse.ArgumentParser(description="Paddler Deployment CLI")
        parser.add_argument("action", choices=["deploy", "remove", "start", "stop", "restart", "add-agent", "remove-agent"],
                            help="Action to perform")
        parser.add_argument("--model", help="Model name (e.g., xxxs)")
        parser.add_argument("--agent", help="Agent name for add-agent/remove-agent")
        args = parser.parse_args()

        if args.model:
            config = next((c for c in self.configs if c['model']['name'] == args.model), None)
            if not config:
                print(f"Model {args.model} not found")
                return
            configs = [config]
        else:
            configs = self.configs

        for config in configs:
            model_name = config['model']['name']
            if args.action == "deploy":
                print(f"Deploying {model_name}")
                self.deploy_instance(config)
            elif args.action == "remove":
                print(f"Removing {model_name}")
                self.remove_instance(config)
            elif args.action in ["start", "stop", "restart"]:
                for agent in config['agents']:
                    if agent['host'] == "localhost":
                        self.manage_service(f"paddler-agent@{model_name}-{agent['name']}", args.action)
                self.manage_service(f"paddler@{model_name}", args.action)
                self.manage_service(f"llama-cpp@{model_name}", args.action)
            elif args.action == "add-agent":
                print(f"Add agent requires updating {model_name}.yaml and re-running 'deploy'")
            elif args.action == "remove-agent":
                if not args.agent:
                    print("Agent name required for remove-agent")
                    return
                if next((a for a in config['agents'] if a['name'] == args.agent), None):
                    if next((a for a in config['agents'] if a['name'] == args.agent and a['host'] == "localhost"), None):
                        self.manage_service(f"paddler-agent@{model_name}-{args.agent}", "stop")
                        self.manage_service(f"paddler-agent@{model_name}-{args.agent}", "disable")
                    print(f"Update {model_name}.yaml to remove agent {args.agent} and run 'deploy'")
                else:
                    print(f"Agent {args.agent} not found in {model_name}")

if __name__ == "__main__":
    os.makedirs(MODELS_DIR, exist_ok=True)
    os.makedirs("/var/lib/paddler", exist_ok=True)
    try:
        subprocess.run(["useradd", "-r", "-s", "/bin/false", "-d", "/var/lib/paddler", PADDLER_USER], check=True)
    except subprocess.CalledProcessError:
        pass
    deployer = PaddlerDeployer()
    deployer.run()