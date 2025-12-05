# Simod Http with Cors

This folder helps with the integration of the [Simod](https://github.com/AutomatedProcessImprovement/Simod) business process simulation model miner.
It includes:
- _A custom HTTP wrapper ([main.py](./main.py)) for Simod 5.1.6 with CORS support._<br>
This version provides a FastAPI-based HTTP interface for Simod 5.1.6, compatible with SimuBridge's architecture. It includes CORS clearance to enable cross-origin requests from the SimuBridge web interface.
- _A [Dockerfile](./Dockerfile) that builds the wrapper on top of the [nokal/simod:5.1.6](https://hub.docker.com/r/nokal/simod) image._<br>
This setup uses the official Simod 5.1.6 Docker image as a base and adds the HTTP API layer on top of it.

## How to run it

### As Part of Docker-Compose 
The easiest way to start the augmented Simod is as part of the docker-compose of the [SimuBridge root project](https://github.com/INSM-TUM/SimuBridge). For this, please refer to the instructions there.

### As Stand-alone Docker Container
To run it as stand-alone docker container, you need to first build the respective docker image by navigating to this folder and calling
``` console
 docker build -t simod-http-cors:2.0.0 .
```

Once the image is built, you can start it with
``` console
docker run -it -p 8880:80 simod-http-cors:2.0.0
```

### For development purposes
To quickly test out changes to the `main.py` without having to rebuild the container, you can mount the file:
``` console
docker run -it -v $PWD/main.py:/app/main.py -p 8880:80 simod-http-cors:2.0.0
```

## What's New in Version 2.0

This version updates the Simod integration from version 3.2.1 to **5.1.6**, bringing:
- Latest Simod features and bug fixes
- Improved model discovery algorithms
- Better performance and reliability
- Updated dependencies and security patches

The HTTP wrapper has been redesigned to work with Simod 5.1.6's architecture while maintaining backward compatibility with SimuBridge's expected API.