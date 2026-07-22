# Use Ubuntu 24.04 as base image (required for ROS2 Jazzy)
FROM ubuntu:24.04

# Set environment variables to avoid interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Lisbon

# --- ENABLE NVIDIA GRAPHICS CAPABILITIES ---
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,display
# ------------------------------------------------

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
    libglvnd0 \
    libgl1 \
    libglx0 \
    libegl1 \
    mesa-utils \
    libglib2.0-0 \
    vim \
    nano \
    iproute2 \
    tmux \
    && rm -rf /var/lib/apt/lists/*

# Set up ROS2 repository
RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
RUN echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | tee /etc/apt/sources.list.d/ros2.list > /dev/null

# Update package lists and install ROS2 Jazzy
# ros-jazzy-ros-gz: official ROS<->Gazebo Harmonic bridge (replaces gazebo-ros-pkgs)
# ros-jazzy-gz-ros2-control: Gazebo Harmonic <-> ros2_control integration (replaces gazebo-ros2-control)
# rosbridge suite for foxglove (exposing websockets)
RUN apt-get update && apt-get install -y \
    ros-jazzy-desktop \
    ros-jazzy-ros-gz \
    ros-jazzy-xacro \
    ros-jazzy-ros2-control \
    ros-jazzy-ros2-controllers \
    ros-jazzy-gz-ros2-control \
    ros-jazzy-robot-localization \
    ros-jazzy-rosbridge-suite \
    python3-rosdep \
    python3-colcon-common-extensions \
    python3-vcstool \
    ros-jazzy-geometry-msgs \
    ros-jazzy-sensor-msgs \
    ros-jazzy-std-msgs \
    ros-jazzy-nav-msgs \
    ros-jazzy-nmea-msgs \
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

# Install GTSAM
WORKDIR /tmp
RUN git clone --branch 4.2.0 --depth 1 https://github.com/borglab/gtsam.git && \
    cd gtsam && \
    mkdir build && cd build && \
    cmake .. \
      -DGTSAM_BUILD_EXAMPLES=OFF \
      -DGTSAM_BUILD_EXAMPLES_ALWAYS=OFF \
      -DGTSAM_BUILD_TESTS=OFF \
      -DGTSAM_BUILD_UNSTABLE=OFF \
      -DGTSAM_USE_SYSTEM_EIGEN=ON \
      -DGTSAM_WITH_TBB=OFF \
      -DCMAKE_INSTALL_PREFIX=/usr/local && \
    make -j$(nproc) && \
    make install && \
    ldconfig

# Install Python libraries
RUN pip3 install --upgrade --ignore-installed pip --break-system-packages
RUN pip3 install --break-system-packages "setuptools<81" wheel
RUN pip3 install --ignore-installed --break-system-packages psutil \
    numpy \
    transforms3d \
    scikit-learn \
    filterpy \
    gtsam \
    ultralytics \
    casadi
RUN pip3 install --break-system-packages --no-build-isolation --ignore-installed bezier

# Set up ROS2 environment
RUN echo "source /opt/ros/jazzy/setup.bash" >> ~/.bashrc

# Create a workspace directory
RUN mkdir -p /opt/share/workspace
WORKDIR /opt/share/workspace

# Copy the project files (uncomment this if you want to copy your project)
# COPY . /opt/share/workspace

# append things from .bashrc.example to .bashrc
COPY .bashrc.example /tmp/.bashrc.example
RUN cat /tmp/.bashrc.example >> ~/.bashrc

# Set the default command to source ROS2 and start bash
CMD ["bash", "-c", "source /opt/ros/jazzy/setup.bash && bash"]