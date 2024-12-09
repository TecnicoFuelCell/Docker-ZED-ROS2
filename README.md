# Docker-ZED

Dockerfile to run _ZED tools_ and the _pyzed_ API with GPU acess. This can/will be updated according to new needs of the TFC team. 

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
