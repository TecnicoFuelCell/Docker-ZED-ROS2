FROM nvcr.io/nvidia/l4t-jetpack:r35.4.1

# Set environment variables to avoid interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Lisbon

# -- Basic dependencies -- #
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
    python3-matplotlib \
    python3-numpy \
    python3-pil \
    python3-psutil \
    python3-requests \
    python3-scipy \
    python3-yaml \
    libopencv-dev \
    libeigen3-dev \
    libgl1-mesa-glx \
    libglib2.0-0 \
    vim \
    nano \
    v4l-utils \
    psmisc \
    usbutils \
    tmux \
    && rm -rf /var/lib/apt/lists/*

# -- Setup ROS2, install ROS2 Foxy and rosdep -- #
RUN curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
RUN echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | tee /etc/apt/sources.list.d/ros2.list > /dev/null

RUN apt-get update && apt-get install -y \
    ros-foxy-desktop \
    python3-rosdep \
    python3-colcon-common-extensions \
    python3-vcstool \
    ros-foxy-geometry-msgs \
    ros-foxy-sensor-msgs \
    ros-foxy-std-msgs \
    ros-foxy-nav-msgs \
    ros-foxy-nmea-msgs \
    ros-foxy-cv-bridge \
    ros-foxy-image-transport \
    ros-foxy-vision-opencv \
    && rm -rf /var/lib/apt/lists/*

RUN rosdep init && rosdep update

# Set up ROS2 environment
RUN echo "source /opt/ros/foxy/setup.bash" >> /root/.bashrc


# -- Install Eigen from source -- #
WORKDIR /tmp
RUN wget https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz && \
tar -xzf eigen-3.4.0.tar.gz && \
cd eigen-3.4.0 && \
mkdir build && cd build && \
cmake .. -DCMAKE_INSTALL_PREFIX=/usr/local && \
make -j$(nproc) && \
make install

# -- Install GTSAM from source -- #
WORKDIR /tmp
RUN git clone --depth 1 --branch release/4.2 https://github.com/borglab/gtsam.git && \
cd gtsam && \
mkdir build && cd build && \
cmake .. \
-DEigen3_DIR=/usr/local/share/eigen3/cmake \
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

# Rebuild the ROS OpenCV bridge against the JetPack OpenCV selected by CMake.
# The Foxy binaries are built against Ubuntu OpenCV 4.2, while JetPack images
# commonly expose OpenCV 4.5. The overlay keeps workspace packages on one ABI.
WORKDIR /opt/vision_opencv_ws/src
RUN git clone --depth 1 --branch foxy https://github.com/ros-perception/vision_opencv.git
WORKDIR /opt/vision_opencv_ws
RUN . /opt/ros/foxy/setup.sh && \
    colcon build --merge-install \
      --packages-select cv_bridge image_geometry \
      --allow-overriding cv_bridge image_geometry
RUN echo "source /opt/vision_opencv_ws/install/setup.bash" >> /root/.bashrc

# -- Torch, Torchvision, pip dependencies -- #
# torch
RUN curl -L -o /tmp/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl \
      https://developer.download.nvidia.cn/compute/redist/jp/v512/pytorch/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl && \
    pip3 install /tmp/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl && \
    rm /tmp/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl

# build torchvision from source --
# dependencies
RUN apt-get update && apt-get install -y \
    libjpeg-dev zlib1g-dev libpython3-dev libopenblas-dev libavcodec-dev libavformat-dev libswscale-dev

# build torchvision; --depth 1 does a shallow clone to avoid downloading the whole history which is super slow
WORKDIR /tmp
# RUN git clone --progress --depth 1 --branch v0.16.0 https://github.com/pytorch/vision.git && \
#     cd vision && \
#     python3 setup.py install --user && \
#     cd .. && rm -rf vision

# build torchvision from source with CUDA ops for Jetson Orin
ENV FORCE_CUDA=1
ENV TORCH_CUDA_ARCH_LIST="8.7"
ENV BUILD_VERSION=0.16.0

WORKDIR /tmp
RUN git clone --progress --depth 1 --branch v0.16.0 https://github.com/pytorch/vision.git && \
    cd vision && \
    python3 -m pip install --no-deps --no-build-isolation -v . && \
    cd .. && rm -rf vision && \
    rm -rf /root/.local/lib/python3.8/site-packages/torchvision*

RUN python3 -m pip install --upgrade pip wheel && \
    python3 -m pip install bezier==2020.1.14 && \
    python3 -m pip install \
    pyserial \
    casadi \
    transforms3d \
    tqdm \
    py-cpuinfo \
    pandas \
    seaborn \
    polars \
    ultralytics-thop && \
    python3 -m pip install --no-deps "ultralytics==8.4"

# foxglove (move above if you rebuild the whole image, here because of layer caching)
RUN apt-get install -y \
    ros-foxy-rosbridge-server \
    lsof

# packages to compress camera images
RUN apt-get install -y \
    ros-foxy-compressed-depth-image-transport \
    ros-foxy-compressed-image-transport \
    ros-foxy-image-transport-plugins \
    ros-foxy-theora-image-transport \
    ros-foxy-xacro

# Create a workspace directory
RUN mkdir -p /opt/share/workspace
WORKDIR /opt/share/workspace

# append things from .bashrc.example to .bashrc
COPY .bashrc.example /tmp/.bashrc.example
RUN cat /tmp/.bashrc.example >> ~/.bashrc

RUN rm -rf /var/lib/apt/lists/*

CMD ["bash", "-lc", "source /opt/ros/foxy/setup.bash && source /opt/vision_opencv_ws/install/setup.bash && exec bash"]
