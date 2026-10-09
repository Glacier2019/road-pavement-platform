cmake_minimum_required(VERSION 3.18)
project(demo_CRG_visualization CXX)

# 用 find_package(Chrono) 让 Chrono 自己解析依赖（VSG / OpenCRG / yaml-cpp …），
# 不要手写 -l。
#
# ★ 2026-10-09 修正：目标名必须用**导出名**，不是源码树里的别名。
#   源码 src/chrono_vehicle/CMakeLists.txt:985 确实有
#       add_library(Chrono::vehicle ALIAS Chrono_vehicle)
#   但 ALIAS 只活在 build tree 里。安装时走的是
#       install(EXPORT ChronoTargets NAMESPACE Chrono::)
#   → 导出名是 Chrono::Chrono_vehicle（= 真实库名 libChrono_vehicle.so）。
#   实测：宿主 ChronoTargets.cmake 里 Chrono::vehicle 出现 0 次。
#   上一版我写了 Chrono::vehicle，于是：
#       CMake Error: Target "demo_CRG_visualization" links to:
#                    Chrono::vehicle
#                    but the target was not found.
#   教训：核对「生产者」不等于核对「消费者」—— 源码里有别名，
#         不代表 install 之后还在。要查就查**装出来的那份** config。
#
# ★ 组件名交给 find_package，ChronoConfig.cmake 会 string(TOUPPER) 之后
#   查 Chrono_<COMPONENT>_AVAILABLE。VEHICLE / VSG 在宿主都已编出。
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(Chrono REQUIRED COMPONENTS vehicle vsg)

add_executable(demo_CRG_visualization 03_visualize.cpp)

# 不再手动 find_package(OpenCRG) 与链 ${OpenCRG_LIBRARY}：
# Chrono 的 imported target 已经把 OpenCRG 带在接口里。
# 手动再链一次不但多余，还会在 OpenCRG_INCLUDE_DIR / OpenCRG_LIBRARY
# 没传进来时直接 REQUIRED 失败 —— 而那正是 04 原来的毛病。
target_link_libraries(demo_CRG_visualization PRIVATE
  Chrono::Chrono_vehicle
  Chrono::Chrono_vsg
)
