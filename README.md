# Local simple environment

This dockerfile aims to provide with only the most essential tools to work with the components of the vehicle, namely Ros2 and Python 3 (and its most used libraries). It is intended to be more accessible since it doesn't require special permissions nor specific hardware. 

The environment does **NOT** have Zed tools.

## Requirements

- [Docker](https://www.docker.com/products/docker-desktop/) installed and running
- Minimum 7Gb free storage space

## Setup

> [!NOTE] 
> For Windows users, you may need to open a terminal and enter WSL to be able to run the following commands.

### 1. Build image

To build the image from the dockerfile, open a terminal at the current directory and run the following command:
```sh
docker build -t <name_you_want_to_give_to_the_image> .
```
for example, you may write `docker build -t tfc-simple-env .`

### 2. Run container

After building the image, you are ready to enter the container and use the tools inside, to do it run this command:
```sh
docker run -it -rm -v <path_to_the_code_repo>:/local-ros2 <name_you_gave_to_the_image>
```
