// ①-3 用 CRGTerrain 加载本平台生成的 .crg 并可视化
//
// API 依据：以下方法名**逐个对装出来的头文件核过**，不是照文档抄的。
//   include/chrono_vehicle/terrain/CRGTerrain.h
//     CRGTerrain(ChSystem*)                     L57
//     void UseMeshVisualization(bool)           L69   true=mesh(路面带) false=boundary(边界曲线)
//     void SimplifyMesh(bool)                   L73
//     void SetContactFrictionCoefficient(float) L77   默认 0.8
//     void Initialize(const std::string&)       L60 注释
//     std::shared_ptr<ChBezierCurve> GetRoadCenterLine()            L103
//     ... GetRoadBoundaryLeft() / GetRoadBoundaryRight()            L106 / L109
//     bool IsPathClosed()                       L115
//     double GetLength() / GetWidth()           L118 / L121
//     void ExportMeshWavefront(const std::string& out_dir)          L142
//   文档页（只当索引用，签名以头文件为准）:
//   https://api.projectchrono.org/classchrono_1_1vehicle_1_1_c_r_g_terrain.html
//
// ★ 2026-10-09 教训：这里原来写着一句
//       vis.EnableAbsFrameCoords();
//   而**这个方法根本不存在** —— 我在整个 include 树里搜不到它，
//   是照"应该有这么个方法"编出来的。编译器直接报：
//       error: 'class chrono::vsg3d::ChVisualSystemVSG' has no member
//              named 'EnableAbsFrameCoords'
//   真实接口是 SetAbsFrameScale(double) + ToggleAbsFrameVisibility()。
//   所以：**方法名一律以装出来的头文件为准**；文档链接只能当索引，
//   不能当签名。写"非猜测"四个字之前，先真的去 grep 一遍。
#include <iostream>
#include <string>
#include <fstream>
#include <filesystem>
#include <system_error>

#include "chrono/physics/ChSystemNSC.h"
#include "chrono/core/ChBezierCurve.h"
#include "chrono_vehicle/terrain/CRGTerrain.h"
#include "chrono_vsg/ChVisualSystemVSG.h"

using namespace chrono;
using namespace chrono::vehicle;
// ★ 2026-10-09：ChVisualSystemVSG 在 chrono::vsg3d 里，不在 chrono 里。
//   chrono_vsg/ChVisualSystemVSG.h:47-48 是 namespace chrono { namespace vsg3d {
//   少了这行会报：
//     error: 'ChVisualSystemVSG' was not declared in this scope;
//            did you mean 'chrono::vsg3d::ChVisualSystemVSG'?
//   （CameraVerticalDir 仍在 chrono 里，chrono/assets/ChVisualSystem.h:42，
//     已由上面的 using namespace chrono 覆盖，不必再引。）
using namespace chrono::vsg3d;

// 把 Bezier 中心线/边界写成 CSV —— 纯 CPU，不需要 GL
static void DumpCurve(const std::shared_ptr<ChBezierCurve>& c, const std::string& fn,
                      const char* label) {
    if (!c) { std::cout << "    (" << label << " 为空)\n"; return; }
    std::ofstream f(fn);
    f << "x,y,z\n";
    const auto& pts = c->GetPoints();
    for (const auto& p : pts) f << p.x() << "," << p.y() << "," << p.z() << "\n";
    std::cout << "    已写出 " << fn << " (" << pts.size() << " 控制点, " << label << ")\n";
}

int main(int argc, char* argv[]) {
    const std::string crg_file = (argc > 1) ? argv[1] : "route_0p1m.crg";
    // ★ 2026-10-09：与 04_run_visualize.sh 的约定对齐 —— 那边写的是
    //     MODE=0   # 0=mesh 1=boundary
    //   而这里原来是 `std::stoi(argv[2]) != 0`，语义正好**反了**：
    //   传 0 得到 boundary，传 1 得到 mesh。沙箱实跑踩到（日志打出
    //   "可视化模式: boundary"，可脚本以为在跑 mesh）。
    //   现改为 `== 0`，与脚本注释一致。
    const bool use_mesh = (argc > 2) ? (std::stoi(argv[2]) == 0) : true;

    std::cout << "==> 加载 CRG: " << crg_file << std::endl;
    std::cout << "    可视化模式: " << (use_mesh ? "mesh(三角网格)" : "boundary(边界曲线)")
              << std::endl;

    ChSystemNSC sys;

    CRGTerrain terrain(&sys);
    terrain.UseMeshVisualization(use_mesh);
    // ★ 摩擦系数无实测来源 —— 见评估文档 §2.2，此处仅取 Chrono 默认值
    terrain.SetContactFrictionCoefficient(0.8f);
    terrain.SimplifyMesh(true);           // 0.1 m 网格很密，简化以利实时渲染
    terrain.Initialize(crg_file);

    std::cout << "    路长 = " << terrain.GetLength() << " m" << std::endl;
    std::cout << "    路宽 = " << terrain.GetWidth()  << " m" << std::endl;
    // ★ 2026-10-09：IsPathClosed() 不是文件里的标志，是 OpenCRG 的**几何猜测**。
    //   crgStatistics.c:258-350 的规则：起点航向与终点航向夹角 < 60°
    //   （divisor = cos(夹角) > 0.5），且两条延长线交在合适位置，就置 uIsClosed=1。
    //   我们这条路航向从 158° 到 156°，只差 2° —— 一条近似直线的路，
    //   两端延长线当然相交，于是被判成"闭合"。OpenCRG 自己的日志写的也是
    //   "reference line may be closed"（*可能*）。
    //   文件里**没有**任何选项能关掉它（dCrgRefLineCloseTrack 是"请求闭合"，
    //   而 crgLoader.c:2455 规定已闭合的路不许再请求闭合，直接 FATAL）。
    //   所以这里不把它当结论报，只当"库的看法"，后面再补一个真事实。
    std::cout << "    闭合(OpenCRG 启发式) = " << (terrain.IsPathClosed() ? "是" : "否")
              << std::endl;

    // ---- 导出（不需要 GL，headless 也能出东西）
    // ★ 2026-10-09：ExportMeshWavefront **不会建目录**。
    //   CRGTerrain.cpp:572-575 就是
    //       ChTriangleMeshConnected::WriteWavefront(out_dir + "/" + name + ".obj", meshes);
    //   目录不存在时它只打印一句 "Unable to create output .OBJ file" 就返回，
    //   而原代码紧接着照样打印「已导出网格 -> chrono_export/」——
    //   又一次"报告成功但没成功"。沙箱实跑就是这么暴露的。
    //   故：先建目录，写完再核实文件真存在，不存在就直说。
    const std::string mesh_dir = "chrono_export";
    std::error_code ec;
    std::filesystem::create_directories(mesh_dir, ec);
    terrain.ExportMeshWavefront(mesh_dir);
    const std::string mesh_file =
        mesh_dir + "/" + std::filesystem::path(crg_file).stem().string() + "_mesh.obj";
    if (std::filesystem::exists(mesh_file)) {
        std::cout << "    已导出网格 -> " << mesh_file
                  << " (" << std::filesystem::file_size(mesh_file) << " 字节)" << std::endl;
    } else {
        std::cout << "    ！网格未导出（" << mesh_file << " 不存在）" << std::endl;
    }
    DumpCurve(terrain.GetRoadCenterLine(),    "chrono_centerline.csv", "中心线");
    DumpCurve(terrain.GetRoadBoundaryLeft(),  "chrono_left.csv",       "左边界");
    DumpCurve(terrain.GetRoadBoundaryRight(), "chrono_right.csv",      "右边界");

    // 首尾控制点间距。注意这是**受上面那个误判影响的产物**，不是真值：
    // Chrono 建 Bezier 曲线时把 m_isClosed 传了进去，closed=true 会在末尾
    // 补回起点，于是这里量出 0。这本身就是误判的可见后果。
    // 真值在 .crg 里：参考线首点 (0,0)、末点 (-4521.122, 3119.874)，相距约 4521 m。
    if (auto cl = terrain.GetRoadCenterLine()) {
        const auto& pts = cl->GetPoints();
        if (pts.size() >= 2) {
            const double wrap = (pts.back() - pts.front()).Length();
            const double span = (pts[pts.size() - 2] - pts.front()).Length();
            std::cout << "    中心线控制点 " << pts.size()
                      << " 个；末点↔首点 = " << wrap << " m，"
                      << "去掉末点后的控制点跨度 = " << span << " m"
                      << "（含切矢手柄，非端点距离）" << std::endl;
            if (wrap < 1.0 && span > 1.0) {
                std::cout << "    ↑ 末点被补回了起点 —— 闭合误判的后果。\n"
                          << "      这条路实际是开口的（.crg 参考线首点 (0,0)、\n"
                          << "      末点 (-4521.122, 3119.874)，相距约 4521 m）。\n"
                          << "      窗口里路末端会多出一片跨约 5.5 km 的三角形（8 个面），\n"
                          << "      就是这条误判拉出来的。详见 README「已知限制」。" << std::endl;
            }
        }
    }

    // ---- 可视化窗口
    ChVisualSystemVSG vis;
    vis.AttachSystem(&sys);
    vis.SetWindowSize(ChVector2i(1600, 900));
    vis.SetWindowTitle("2025Y095 - CRG road (Chrono::Vehicle)");
    vis.SetCameraVertical(CameraVerticalDir::Z);
    vis.AddCamera(ChVector3d(-120, -260, 120), ChVector3d(0, 0, 0));
    // 显示世界原点坐标系，便于判断路面的绝对方位。
    // 默认是关的（ChVisualSystemVSG.cpp:322  m_show_abs_frame(false)），
    // 所以 Toggle 一次即打开。它只有切换形式，没有显式 bool 重载。
    vis.SetAbsFrameScale(50.0);
    vis.ToggleAbsFrameVisibility();
    vis.Initialize();

    std::cout << "\n==> 窗口已开。鼠标左键=旋转 右键=平移 滚轮=缩放 Q/ESC=退出\n" << std::endl;

    while (vis.Run()) {
        vis.BeginScene();
        vis.Render();
        vis.EndScene();
    }
    return 0;
}
