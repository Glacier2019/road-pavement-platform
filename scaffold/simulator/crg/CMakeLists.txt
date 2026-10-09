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

# ★ 2026-10-09：不要给 find_package 加 models 组件。
#   我一度写了 COMPONENTS vehicle vsg models，查了装出来的 config 才发现
#   **Chrono_MODELS_AVAILABLE 根本不存在** —— 可用组件只有这 20 个：
#     CASCADE CSHARP DEM FMI FSI FSI_SPH FSI_TDPF IRRLICHT MODAL MULTICORE
#     MUMPS PARDISOMKL PARSERS POSTPROCESS ROS SENSOR SYNCHRONO VEHICLE
#     VEHICLECOSIM VSG
#   加了会直接 "missing components: models" 配置失败。
#   但目标 Chrono::ChronoModels_vehicle **确实是导出的**
#   （ChronoTargets.cmake 里有 add_library(Chrono::ChronoModels_vehicle …)，
#     libChronoModels_vehicle.so 6.6 MB 在位）—— 所以直接链就行，无需声明组件。
#   教训又一次：**能链的目标 ≠ 可声明的组件**，两套名字各查一遍。

# ---------------------------------------------------------------------------
# 目标一：路面可视化（只有路面，没有车）
# ---------------------------------------------------------------------------
add_executable(demo_CRG_visualization 03_visualize.cpp)

# 不再手动 find_package(OpenCRG) 与链 ${OpenCRG_LIBRARY}：
# Chrono 的 imported target 已经把 OpenCRG 带在接口里。
# 手动再链一次不但多余，还会在 OpenCRG_INCLUDE_DIR / OpenCRG_LIBRARY
# 没传进来时直接 REQUIRED 失败 —— 而那正是 04 原来的毛病。
target_link_libraries(demo_CRG_visualization PRIVATE
  Chrono::Chrono_vehicle
  Chrono::Chrono_vsg
)

# ---------------------------------------------------------------------------
# 目标二：CRG 路面上的整车仿真（第 1 步后半段 —— 让车真的在路上跑）
#
# ★ ChronoModels_vehicle 是 HMMWV/sedan/gclass 等 20 个车型所在的库。
#   03 不需要它，05 必须链 —— 少了会在链接期报 undefined reference，
#   那还是好的；真正难查的是**编过了但运行时读不到车型 JSON**，
#   那属于数据路径问题（见 05 文件头「坑 B」）。
# ---------------------------------------------------------------------------
add_executable(demo_vehicle_on_crg 05_vehicle_on_crg.cpp)

# ★ 2026-10-09 实测补链：Chrono_vehicle_vsg 是**必须的第三个**。
#   漏了它的症状是链接期一堆
#       undefined reference to `vtable for ChWheeledVehicleVisualSystemVSG'
#       undefined reference to `ChVehicleVisualSystemVSG::~ChVehicleVisualSystemVSG()'
#   —— 因为 ChWheeledVehicleVisualSystemVSG / ChVehicleVisualSystemVSG
#   这一对「车 + VSG」的胶水类住在 libChrono_vehicle_vsg.so 里，
#   既不在 Chrono_vehicle，也不在 Chrono_vsg。
#   头文件在 chrono_vehicle/wheeled_vehicle/ 下，很容易误以为链 vehicle 就够。
target_link_libraries(demo_vehicle_on_crg PRIVATE
  Chrono::Chrono_vehicle
  Chrono::Chrono_vehicle_vsg
  Chrono::ChronoModels_vehicle
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
#
# ★ 两个目标都要这一个宏 —— 05 还用它推导车辆数据目录
#   （<CHRONO_DATA_DIR>/vehicle/），所以不能只给 03 加。
#
# ★ 2026-10-09：Chrono 其实**还导出了 CHRONO_VEHICLE_DATA_DIR**
#   （配置输出实测 = .../share/chrono/data/vehicle/，带尾斜杠）。
#   05 优先用它，只有在它缺失时才退回「CHRONO_DATA_DIR + vehicle/」的推导。
#   两个都烘进去，源码里用 #ifdef 选。
get_filename_component(CHRONO_DATA_DIR_ABS "${CHRONO_DATA_DIR}" ABSOLUTE)
get_filename_component(CHRONO_VEHICLE_DATA_DIR_ABS "${CHRONO_VEHICLE_DATA_DIR}" ABSOLUTE)
foreach(tgt demo_CRG_visualization demo_vehicle_on_crg)
  target_compile_definitions(${tgt} PRIVATE
    "CHRONO_DATA_DIR=\"${CHRONO_DATA_DIR_ABS}\""
  )
endforeach()
target_compile_definitions(demo_vehicle_on_crg PRIVATE
  # ★ 用**自己的宏名**，不覆盖上游的 CHRONO_VEHICLE_DATA_DIR。
  #   ChronoTargets.cmake:83 把 CHRONO_VEHICLE_DATA_DIR 定成 <前缀>/data/vehicle/，
  #   而本机数据在 <前缀>/share/chrono/data/vehicle/ —— 上游那条是错的。
  #   直接覆盖只会得到一句 `warning: redefined`，警告是唯一提示、路径错却是致命的。
  "WIM_CHRONO_VEHICLE_DATA_DIR=\"${CHRONO_VEHICLE_DATA_DIR_ABS}\""
)
