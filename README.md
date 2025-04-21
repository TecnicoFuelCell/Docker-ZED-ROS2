# Docker-ZED

Dockerfile to run _ZED tools_ ,the _pyzed_ API, _ROS2 Foxy_ and ultralytics with GPU acess. This can/will be updated according to new needs of the TFC team. 

## Prerequisites
Before running this script, ensure:

1. Docker is installed and running on your system.
2. You have NVIDIA drivers and the NVIDIA Container Toolkit installed for GPU support.
3. You have a working directory prepared for binding with the container.
3. For linux, run the following lines:
```bash
xhost +local:docker
```

## Script Procedure
- Run the lin script (tested in Linux and WSL2)

## No Script Procedure
* Download the docker file
* On the directory where the docker file is, open a terminal (not a wsl one) and write:
```bash
docker build -t <name_you_want_to_give_to_the_image> .
```
* Open xLaunch and select the display number as 0 and proceed with the pre-selected things
* Create on the same directory as the Dockerfile a directory /share/catkin_ws/src
* Create the container as follows:

```bash
docker run --name <name_you_want_to_give_to_the_container> --privileged --gpus all -v /dev:/dev -it -v "<your_workspace_path>:/opt/share/workspace" -env="DISPLAY" --env="QT_X11_NO_MITSHM=1" --volume="/tmp/.X11-unix:/tmp/.X11-unix:rw" <name_of_your_image>
```

## Inside Jetson

To be able to run the models, you can create a container using `build_lin.sh` at `~/Documents/Docker-Zed-ROS2`. To create it, follow instructions at [Creating a Container](#creating-a-container). To use the existing one (`testfc`):

```bash
docker start testfc
docker exec -it testfc /bin/bash
```


### Creating a Container
```bash
cd ~/Documents/Docker-Zed-ROS2
./build_lin.sh
> Enter the name of your Docker image: <put the name you want>
> Enter the name of your Docker container: <put the name you want>
> Enter the path to your workspace: /home/tfcadmin/Documents/Autonomous_Systems/ros2_ws
```
