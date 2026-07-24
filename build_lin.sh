#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
current_dir="$PWD"

# defaults (fallback) - ALTERADOS PARA O SEU PROJETO
default_image="autonomnom_ws"
default_container="autonomnom_container"
default_workspace="$(realpath -m "$current_dir/../Autonomous_Systems/ros2_ws")"

read -rp "Docker image name [$default_image]: " image_name
image_name="${image_name:-$default_image}"

read -rp "Container name [$default_container]: " container_name
container_name="${container_name:-$default_container}"

read -rp "Workspace path to mount [$default_workspace]: " workspace_input
workspace_input="${workspace_input:-$default_workspace}"

if [[ "$workspace_input" = /* ]]; then
  workspace="$workspace_input"
else
  workspace="$(realpath -m "$current_dir/$workspace_input")"
fi

read -rp "Enable GUI/X11 forwarding? (Y/n): " use_gui
use_gui="${use_gui:-Y}"

default_gpu="N"
if [[ "$image_name" =~ cuda ]]; then
  default_gpu="Y"
fi

read -rp "Enable GPU passthrough? (${default_gpu}/n): " use_gpu
use_gpu="${use_gpu:-$default_gpu}"

read -rp "Privilege the container (for hardware access)? (y/N): " use_privileged
use_privileged="${use_privileged:-N}"

read -rp "Connect USB devices (/dev/ttyACM*)? (y/N): " use_usb
use_usb="${use_usb:-N}"

if [[ ! -d "$workspace" ]]; then
  echo "Error: workspace path does not exist: $workspace"
  exit 1
fi

# if the image exists skip building it otherwise build it
if docker image inspect "$image_name" >/dev/null 2>&1; then
  echo "Image '$image_name' already exists. Skipping build."
else
  echo "Building image: $image_name"
  docker build -t "$image_name" -f "$script_dir/Dockerfile" "$workspace"
fi

run_args=(
  -it
  --name "$container_name"
  --network host
  # MUDANÇA AQUI: Monta apenas o 'src' para não apagar o 'install' do Docker
  -v "$workspace/src:/opt/share/workspace/src"
)

if [[ "$use_usb" =~ ^[Yy]$ ]]; then
  run_args+=(--device /dev/ttyACM*)
fi

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
    -e "__NV_PRIME_RENDER_OFFLOAD=1"
    -e "__GLX_VENDOR_LIBRARY_NAME=nvidia"
    -v "/tmp/.X11-unix:/tmp/.X11-unix:rw"
  )

  if [ -d "/dev/dri" ]; then
    run_args+=(--device /dev/dri:/dev/dri)
  fi
fi

echo "Starting container: $container_name"
docker run "${run_args[@]}" "$image_name"
