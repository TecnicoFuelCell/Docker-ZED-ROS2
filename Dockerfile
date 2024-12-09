FROM stereolabs/zed:4.2-gl-devel-cuda11.4-ubuntu20.04 

# Set the working directory to the workspace
WORKDIR /opt/share/workspace

RUN rm -rf var/lib/apt/lists/*

RUN echo "All done!"