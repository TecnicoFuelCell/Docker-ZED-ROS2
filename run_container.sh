#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
current_dir="$PWD"

# defaults (fallback)
default_image="sim"
default_container="sim"

default_workspace="$(realpath -m "$current_dir/../Autonomous_Systems/ros2_ws")"

read -rp "Docker image name [$default_image]: " image_name
image_name="${image_name:-$default_image}"

read -rp "Container name [$default_container]: " container_name
container_name="${container_name:-$default_container}"

# --- NEW: detect an existing container with this name ---
if docker container inspect "$container_name" >/dev/null 2>&1; then
    container_state="$(docker container inspect -f '{{.State.Status}}' "$container_name")"
    echo "A container named '$container_name' already exists (state: $container_state)."
    read -rp "Attach to it instead of creating a new one? (Y/n): " reuse_choice
    reuse_choice="${reuse_choice:-Y}"

    if [[ "$reuse_choice" =~ ^[Yy]$ ]]; then
        if [[ "$container_state" != "running" ]]; then
            echo "Starting existing container: $container_name"
            docker start "$container_name" >/dev/null
        fi
        echo "Attaching to container: $container_name"
        exec docker exec -it "$container_name" bash
    else
        read -rp "Remove the existing container and create a fresh one? (y/N): " remove_choice
        remove_choice="${remove_choice:-N}"
        if [[ "$remove_choice" =~ ^[Yy]$ ]]; then
            docker rm -f "$container_name" >/dev/null
            echo "Removed old container: $container_name"
        else
            echo "Aborting: container name '$container_name' is already in use."
            exit 1
        fi
    fi
fi
# --- end new block ---

read -rp "Workspace path to mount [$default_workspace]: " workspace_input
workspace_input="${workspace_input:-$default_workspace}"

if [[ "$workspace_input" = /* ]]; then
    workspace="$workspace_input"
else
    workspace="$(realpath -m "$current_dir/$workspace_input")"
fi

read -rp "Enable GUI/X11 forwarding? (y/N): " use_gui
use_gui="${use_gui:-N}"

default_gpu="N"
if [[ "$image_name" =~ cuda ]]; then
    default_gpu="Y"
fi

if [[ "$default_gpu" == "Y" ]]; then
    gpu_prompt="Y/n"
else
    gpu_prompt="y/N"
fi

read -rp "Enable GPU passthrough? (${gpu_prompt}): " use_gpu
use_gpu="${use_gpu:-$default_gpu}"

read -rp "Privilege the container (for hardware access)? (y/N): " use_privileged
use_privileged="${use_privileged:-N}"

if [[ ! -d "$workspace" ]]; then
    echo "Error: workspace path does not exist: $workspace"
    exit 1
fi

# if the image exists skip building it otherwise build it
if docker image inspect "$image_name" >/dev/null 2>&1; then
    echo "Image '$image_name' already exists. Skipping build."
else
    echo "Building image: $image_name"
    docker build -t "$image_name" "$script_dir"
fi

run_args=(
    #--rm
    -it
    --name "$container_name"
    --network host
    -v "$workspace:/opt/share/workspace"
)

if [[ "$use_gpu" =~ ^[Yy]$ ]]; then
    run_args+=(--gpus all)
fi

if [[ "$use_privileged" =~ ^[Yy]$ ]]; then
    run_args+=(--privileged)
    run_args+=(-v "/dev:/dev")
fi

if [[ "$use_gui" =~ ^[Yy]$ ]]; then
    xhost +local:docker >/dev/null
    trap 'xhost -local:docker >/dev/null' EXIT

    run_args+=(
        -e "DISPLAY=$DISPLAY"
        -e "QT_X11_NO_MITSHM=1"
        -v "/tmp/.X11-unix:/tmp/.X11-unix:rw"
    )

    # NVIDIA-specific render-offload vars only make sense if GPU passthrough
    # was actually requested - forcing them on a non-GPU run can break rendering.
    if [[ "$use_gpu" =~ ^[Yy]$ ]]; then
        run_args+=(
            -e "__NV_PRIME_RENDER_OFFLOAD=1"
            -e "__GLX_VENDOR_LIBRARY_NAME=nvidia"
        )
    fi

    if [ -d "/dev/dri" ]; then
        run_args+=(--device /dev/dri:/dev/dri)
    fi
fi

echo "Starting container: $container_name"
docker run "${run_args[@]}" "$image_name"