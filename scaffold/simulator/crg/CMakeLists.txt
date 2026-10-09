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

# ★ 2026-10-09：把 Chrono 数据目录烘进可执行文件。
#
#   不加这一条的后果是**段错误**，而且现场离原因很远：
#   ChDataPath.cpp 的默认值是硬编码的相对路径 "../data/"，
#   ChVisualSystemVSG 构造时把它 push 进 VSG 的搜索路径，
#   Initialize() 拿它去读 vsg/fonts/OpenSans-Bold.vsgb → 读不到 →
#   打印 "Failed to read font" 之后**直接 return**，窗口和 viewer 都没建，
#   而 Run() 就是 `return m_viewer->active();` → 空指针解引用。
#
#   CHRONO_DATA_DIR 是 ChronoConfig.cmake 给消费者准备的变量
#   （宿主实测值 .../chrono/lib/cmake/Chrono/../../../share/chrono/data/），
#   find_package(Chrono) 之后即可用。绝对化一下，免得带 .. 看着心里没底。
#
#   官方同款写法见 chrono/template_project/CMakeLists.txt:107。
get_filename_component(CHRONO_DATA_DIR_ABS "${CHRONO_DATA_DIR}" ABSOLUTE)
target_compile_definitions(demo_CRG_visualization PRIVATE
  "CHRONO_DATA_DIR=\"${CHRONO_DATA_DIR_ABS}\""
)
