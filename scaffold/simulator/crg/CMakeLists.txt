cmake_minimum_required(VERSION 3.18)
project(demo_CRG_visualization CXX)

# 用 CMake 找 Chrono，而不是手写 -l 链接。
# 好处：Chrono 的依赖（VSG、OpenCRG、各模块间接依赖）由它自己解析，
#       官方推荐写法即 find_package(Chrono) + target_link_libraries(Chrono::xxx)。
# 目标名已核对官方源码：
#   src/chrono_vehicle/CMakeLists.txt:  add_library(Chrono_vehicle) + ALIAS Chrono::vehicle
#   src/chrono_vsg/CMakeLists.txt:      add_library(Chrono_vsg)     + ALIAS Chrono::vsg
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(Chrono REQUIRED)
find_package(OpenCRG REQUIRED)

message(STATUS "Chrono  include: ${Chrono_INCLUDE_DIRS}")
message(STATUS "OpenCRG include: ${OpenCRG_INCLUDE_DIR}")
message(STATUS "OpenCRG library: ${OpenCRG_LIBRARY}")

add_executable(demo_CRG_visualization 03_visualize.cpp)
target_include_directories(demo_CRG_visualization PRIVATE ${Chrono_INCLUDE_DIRS})
target_link_libraries(demo_CRG_visualization PRIVATE
  Chrono::vehicle
  Chrono::vsg
  ${OpenCRG_LIBRARY}
)
