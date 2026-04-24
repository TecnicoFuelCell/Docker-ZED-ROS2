#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
current_dir="$PWD"

# defaults (fallback)
default_image="local-simple-env"
default_container="local-simple-env"

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

read -rp "Enable GUI/X11 forwarding? (y/N): " use_gui
use_gui="${use_gui:-N}"

default_gpu="N"
if [[ "$image_name" =~ cuda ]]; then
  default_gpu="Y"
fi

read -rp "Enable GPU passthrough? (${default_gpu}/n): " use_gpu
use_gpu="${use_gpu:-$default_gpu}"

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

if [[ "$use_gui" =~ ^[Yy]$ ]]; then
  xhost +local:docker >/dev/null
  trap 'xhost -local:docker >/dev/null' EXIT
  run_args+=(
    -e "DISPLAY=$DISPLAY"
    -v "/tmp/.X11-unix:/tmp/.X11-unix:rw"
  )
fi

echo "Starting container: $container_name"
docker run "${run_args[@]}" "$image_name"
