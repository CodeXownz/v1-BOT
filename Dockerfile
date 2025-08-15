# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /usr/src/app

# Install system dependencies needed for Docker and tmate
RUN apt-get update && apt-get install -y \
    procps \
    iproute2 \
    openssh-client \
    tmate \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file and install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire bot code into the container
COPY . .

# Command to run the Python script
CMD ["python", "v1.py"]
