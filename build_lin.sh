#!/bin/bash

# Default workspace path
default_workspace="/home/tfcadmin/Documents/Autonomous_Systems/ros2_ws"

# Prompt the user for image and container names
read -p "Enter the name of your Docker image: " image_name
read -p "Enter the name you want to give to the Docker container: " container_name
read -p "Enter the path to the workspace directory [${default_workspace}]: " workspace

# Fallback to default if empty
workspace="${workspace:-$default_workspace}"

# Ask whether to build the image
read -p "Is the Docker image already built? [y/N]: " use_existing
use_existing=${use_existing,,}  # to lowercase

# Set the working directory to where the script is located
scriptPath="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Remove previous container first
docker stop "$container_name" >/dev/null 2>&1
docker rm "$container_name" >/dev/null 2>&1

# Build image only if user says it's not already built
if [[ "$use_existing" != "y" ]]; then
    echo "Building the Docker image '$image_name'..."
    docker build --network host -t "$image_name" .

    if [ $? -ne 0 ]; then
        echo "❌ Failed to build Docker image '$image_name'."
        exit 1
    fi
    echo "✅ Docker image '$image_name' built successfully."
fi

# Check if the Docker image was built successfully
if [ $? -eq 0 ]; then
    echo "Docker image '$image_name' built successfully."

    # Run the Docker container with the specified names
    echo "Running the Docker container '$container_name'..."

    docker run -d -it \
        --name "$container_name" \
        --privileged \
        --runtime nvidia \
        --restart unless-stopped \
        --gpus all \
        --network host \
        -e DISPLAY=$DISPLAY \
        -e NVIDIA_VISIBLE_DEVICES=all \
        -e NVIDIA_DRIVER_CAPABILITIES=graphics,utility,video \
        -e XDG_RUNTIME_DIR=/tmp/runtime-root \
        -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
        -v /tmp:/tmp:rw \
        -v /var/nvidia/nvcam/settings:/var/nvidia/nvcam/settings:ro \
        -v /etc/systemd/system/zed_x_daemon.service:/etc/systemd/system/zed_x_daemon.service:ro \
        -v $HOME/.Xauthority:/root/.Xauthority \
        -e XAUTHORITY=/root/.Xauthority \
        -v "$workspace:/opt/share/workspace" \
        -v /var/run/argus-daemon:/var/run/argus-daemon:rw \
        --device /dev/video0 \
        --device /dev/video1 \
        --device /dev/dri:/dev/dri \
	    -v /dev:/dev \
        "$image_name"

    # Check if the Docker container ran successfully
    if [ $? -eq 0 ]; then
        echo "Docker container '$container_name' started successfully."
        # enter docker container via exec (to bash)
        docker exec -it "$container_name" bash
    else
        echo "Failed to start Docker container '$container_name'."
    fi

else
    echo "Failed to build Docker image '$image_name'."
fi
