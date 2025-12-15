# Use Ubuntu 20.04 as base image (required for ROS2 Foxy)
FROM ubuntu:20.04

# Set environment variables to avoid interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Lisbon

# Update package lists and install basic dependencies
RUN apt-get update && apt-get install -y \
    curl \
    gnupg2 \
    lsb-release \
    wget \
    software-properties-common \
    build-essential \
    git \
    python3 \
    python3-pip \
    python3-dev \
    python3-opencv \
    libopencv-dev \
    libeigen3-dev \
    vim \
    nano \
    && rm -rf /var/lib/apt/lists/*

# Set up ROS2 repository
RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
RUN echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | tee /etc/apt/sources.list.d/ros2.list > /dev/null

# Update package lists and install ROS2 Foxy
RUN apt-get update && apt-get install -y \
    ros-foxy-desktop \
    python3-rosdep \
    python3-colcon-common-extensions \
    python3-vcstool \
    ros-foxy-geometry-msgs \
    ros-foxy-sensor-msgs \
    ros-foxy-std-msgs \
    ros-foxy-nav-msgs \
    && rm -rf /var/lib/apt/lists/*

# Initialize rosdep
RUN rosdep init && rosdep update

# Install Eigen from source
WORKDIR /tmp
RUN wget https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz && \
    tar -xzf eigen-3.4.0.tar.gz && \
    cd eigen-3.4.0 && \
    mkdir build && cd build && \
    cmake .. -DCMAKE_INSTALL_PREFIX=/usr/local && \
    make -j$(nproc) && \
    make install

# install GTSAM
WORKDIR /tmp
RUN git clone https://github.com/borglab/gtsam.git && \
    cd gtsam && \
    sed -i 's/\bint m_runtime;/[[maybe_unused]] int m_runtime;/' gtsam/navigation/ManifoldEKF.h && \
    mkdir build && cd build && \
    cmake .. \
      -DGTSAM_BUILD_EXAMPLES=OFF \
      -DGTSAM_BUILD_EXAMPLES_ALWAYS=OFF \
      -DGTSAM_BUILD_TESTS=OFF \
      -DGTSAM_USE_SYSTEM_EIGEN=ON \
      -DCMAKE_INSTALL_PREFIX=/usr/local && \
    make -j$(nproc) && \
    make install

# Install Python libraries
RUN pip3 install --upgrade pip
RUN pip3 install \
    opencv-python \
    numpy \
    transforms3d \
    scikit-learn \
    filterpy

# Set up ROS2 environment
RUN echo "source /opt/ros/foxy/setup.bash" >> ~/.bashrc

# Create a workspace directory
RUN mkdir -p /local-ros2
WORKDIR /local-ros2

# Copy the project files (uncomment this if you want to copy your project)
# COPY . /local-ros2/

# Set the default command to source ROS2 and start bash
CMD ["bash", "-c", "source /opt/ros/foxy/setup.bash && bash"]