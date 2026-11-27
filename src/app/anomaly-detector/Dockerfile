FROM python:3.10

WORKDIR /usr/src/app

# 模型脚本（embeddings CSV 由 initContainer 从 OBS 下载，见 k8s manifest）
COPY anomaly_detector.py embeddings_loader.py download_embeddings.py requirements.txt ./

RUN pip install -r requirements.txt --no-cache-dir

CMD [ "python3", "-u", "anomaly_detector.py" ]
