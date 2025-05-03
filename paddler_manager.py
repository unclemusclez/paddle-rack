import yaml
import requests
from prometheus_client import start_http_server, Gauge
from statsd import StatsClient
import time
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

class PaddlerInventory:
    def __init__(self, inventory_file="inventory.yaml"):
        self.inventory_file = inventory_file
        self.instances = self.load_inventory()

    def load_inventory(self):
        with open(self.inventory_file, 'r') as f:
            data = yaml.safe_load(f)
        return data['paddler_instances']

    def list_instances(self):
        for instance in self.instances:
            print(f"Name: {instance['name']}, Model: {instance['model']}, "
                  f"API: {instance['api_endpoint']}, StatsD: {instance['statsd_prefix']}@{instance['statsd_addr']}")

    def get_instance(self, name):
        return next((inst for inst in self.instances if inst['name'] == name), None)

class PaddlerMetricsExporter:
    def __init__(self, inventory, port=8000, poll_interval=10):
        self.inventory = inventory
        self.port = port
        self.poll_interval = poll_interval
        # Prometheus gauges
        self.slots_idle = Gauge('paddler_slots_idle', 'Idle slots per Paddler instance', ['instance', 'model'])
        self.slots_processing = Gauge('paddler_slots_processing', 'Processing slots per Paddler instance', ['instance', 'model'])

    def fetch_agent_metrics(self, instance):
        try:
            response = requests.get(instance['api_endpoint'], timeout=5)
            response.raise_for_status()
            data = response.json()
            # Assuming UpstreamPeerPool JSON structure includes slots_idle and slots_processing
            # Adjust based on actual JSON response structure
            slots_idle = sum(agent.get('slots_idle', 0) for agent in data.get('agents', []))
            slots_processing = sum(agent.get('slots_processing', 0) for agent in data.get('agents', []))
            return slots_idle, slots_processing
        except Exception as e:
            print(f"Error fetching metrics for {instance['name']}: {e}")
            return 0, 0

    def run(self):
        start_http_server(self.port)
        print(f"Metrics exporter running on http://localhost:{self.port}/metrics")
        while True:
            for instance in self.inventory.instances:
                slots_idle, slots_processing = self.fetch_agent_metrics(instance)
                self.slots_idle.labels(instance['name'], instance['model']).set(slots_idle)
                self.slots_processing.labels(instance['name'], instance['model']).set(slots_processing)
            time.sleep(self.poll_interval)

class PaddlerManager(PaddlerInventory):
    def __init__(self, grafana_url="http://localhost:3000", metrics_port=8000):
        super().__init__()
        self.grafana_url = grafana_url
        self.statsd_clients = {
            inst['name']: StatsClient(host=inst['statsd_addr'].split(':')[0],
                                     port=int(inst['statsd_addr'].split(':')[1]),
                                     prefix=inst['statsd_prefix'])
            for inst in self.instances
        }
        self.exporter = PaddlerMetricsExporter(self, port=metrics_port)

    def start_exporter(self):
        threading.Thread(target=self.exporter.run, daemon=True).start()

    def query_api(self, instance_name):
        instance = self.get_instance(instance_name)
        if not instance:
            print(f"Instance {instance_name} not found")
            return
        try:
            response = requests.get(instance['api_endpoint'], timeout=5)
            response.raise_for_status()
            print(f"API Response for {instance_name}:\n{response.json()}")
        except Exception as e:
            print(f"Error querying API for {instance_name}: {e}")

    def open_grafana(self):
        webbrowser.open(self.grafana_url)

    def run(self):
        self.start_exporter()
        print("Paddler Management System")
        while True:
            print("\n1. List Instances\n2. Query API\n3. Open Grafana\n4. Exit")
            choice = input("Choose an option: ")
            if choice == '1':
                self.list_instances()
            elif choice == '2':
                name = input("Enter instance name: ")
                self.query_api(name)
            elif choice == '3':
                self.open_grafana()
            elif choice == '4':
                break

if __name__ == "__main__":
    manager = PaddlerManager()
    manager.run()