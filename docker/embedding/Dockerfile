FROM python:3.12-slim

# hf-mirror.com is reachable from the campus network while huggingface.co is not.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    MODEL_CACHE_DIR=/models \
    OMP_NUM_THREADS=8 \
    PORT=8000

WORKDIR /srv

# torch comes from the Aliyun mirror of the official CPU wheel index. download.pytorch.org
# is reachable but measured ~170 KB/s here, while this mirror serves ~12 MB/s. The mirror
# is a plain file listing rather than a PEP 503 index, so the wheel is fetched by direct
# URL and the Tsinghua PyPI mirror resolves its dependencies.
RUN pip install --no-cache-dir --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        "https://mirrors.aliyun.com/pytorch-wheels/cpu/torch-2.4.1%2Bcpu-cp312-cp312-linux_x86_64.whl" \
 && pip install --no-cache-dir --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        "fastapi==0.115.6" \
        "uvicorn[standard]==0.34.0" \
        "sentence-transformers==3.3.1" \
        "huggingface_hub==0.27.1"

COPY app.py /srv/app.py

EXPOSE 8000

CMD ["python", "app.py"]
