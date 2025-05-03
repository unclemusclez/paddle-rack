FROM python:3.9-slim
WORKDIR /app
COPY paddler_manager.py inventory.yaml requirements.txt ./
RUN pip install -r requirements.txt
CMD ["python", "paddler_manager.py"]