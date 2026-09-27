FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-tam tesseract-ocr-eng poppler-utils gdal-bin python3 && rm -rf /var/lib/apt/lists/*
