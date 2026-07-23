# =============================================================================
# Dockerfile - ros2_ws (ROS 2 Jazzy / Ubuntu 24.04 / Python 3.12)
# =============================================================================

FROM osrf/ros:jazzy-desktop

ENV DEBIAN_FRONTEND=noninteractive
ENV ROS_DISTRO=jazzy
SHELL ["/bin/bash", "-c"]

# -----------------------------------------------------------------------------
# 1. Dependências de sistema base (Eigen 3.4 já vem no Ubuntu 24.04)
# -----------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git wget curl unzip \
        libboost-all-dev libtbb-dev libopencv-dev \
        python3-pip python3-colcon-common-extensions python3-rosdep python3-vcstool \
        libeigen3-dev \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# 2. GTSAM a partir do source, instalado em /usr/local
# -----------------------------------------------------------------------------
RUN git clone --branch 4.2 --depth 1 https://github.com/borglab/gtsam.git /tmp/gtsam \
    && cd /tmp/gtsam && mkdir build && cd build \
    && cmake .. \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DGTSAM_USE_SYSTEM_EIGEN=ON \
        -DGTSAM_BUILD_TESTS=OFF \
        -DGTSAM_BUILD_EXAMPLES_ALWAYS=OFF \
        -DGTSAM_WITH_TBB=ON \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig \
    && rm -rf /tmp/gtsam

# -----------------------------------------------------------------------------
# 3. Dependências ROS declaradas nos package.xml
# -----------------------------------------------------------------------------
WORKDIR /opt/share/workspace
COPY ros2_ws/src ./src
COPY ros2_ws/description ./description

RUN apt-get update \
    && rosdep update \
    && rosdep install --from-paths src --ignore-src -r -y \
        --skip-keys "gtsam" \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# 4. Dependências Python (Python 3.12 nativo do Jazzy aceita todas)
# -----------------------------------------------------------------------------
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed \
        casadi \
        bezier \
        transforms3d \
        rosbags \
        ultralytics \
        torch --extra-index-url https://download.pytorch.org/whl/cpu

# -----------------------------------------------------------------------------
# 5. Build do workspace
# -----------------------------------------------------------------------------
RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
    && colcon build --symlink-install

# -----------------------------------------------------------------------------
# 6. Entrypoint
# -----------------------------------------------------------------------------
RUN printf '#!/bin/bash\nset -e\nsource /opt/ros/jazzy/setup.bash\nif [ -f /opt/share/workspace/install/setup.bash ]; then\n    source /opt/share/workspace/install/setup.bash\nfi\nexec "$@"\n' > /ros_entrypoint.sh \
    && chmod +x /ros_entrypoint.sh

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
