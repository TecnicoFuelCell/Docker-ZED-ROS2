FROM stereolabs/zed:4.2-gl-devel-cuda11.4-ubuntu20.04 

ENV DEBIAN_FRONTEND=noninteractive

# Update and install essential packages
RUN apt update && apt install -y \
    apt-utils \
    locales \
    gnupg2 \
    lsb-release \
    curl \
    software-properties-common && \
    locale-gen en_US en_US.UTF-8 && \
    update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 && \
    export LANG=en_US.UTF-8

# Install Foxy
RUN add-apt-repository universe && \
    curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null 


RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key | apt-key add - && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu focal main" > /etc/apt/sources.list.d/ros2-latest.list


RUN apt update && apt upgrade -y && \
    apt install -y ros-foxy-desktop python3-argcomplete && \
    apt install -y python3-colcon-common-extensions

#Source the project
RUN echo "source /opt/ros/foxy/setup.bash" >> /etc/bash.bashrc
RUN echo "source /opt/share/workspace/install/setup.bash" >> ~/.bashrc

# Other usefull libraries
RUN apt-get update && apt-get install -y \
    git python3-pip tmux nano x11-apps\
    build-essential

RUN pip install opencv-python pygame ultralytics pyserial 

# Set the working directory to the workspace
WORKDIR /opt/share/workspace

RUN rm -rf var/lib/apt/lists/*

RUN echo "All done!"