// ①-3 用 CRGTerrain 加载本平台生成的 .crg 并可视化
//
// API 依据（均取自官方类参考，非猜测）:
//   https://api.projectchrono.org/classchrono_1_1vehicle_1_1_c_r_g_terrain.html
//     CRGTerrain(ChSystem*)
//     void UseMeshVisualization(bool)          // true=mesh(路面带) false=boundary(边界曲线)
//     void SetContactFrictionCoefficient(float) // 默认 0.8
//     void Initialize(const std::string& crg_file)
//     double GetLength() / GetWidth()
//     void ExportMeshWavefront(const std::string& out_dir)
//     std::shared_ptr<ChBezierCurve> GetRoadCenterLine()
//     std::shared_ptr<ChBezierCurve> GetRoadBoundaryLeft() / GetRoadBoundaryRight()
//     void SimplifyMesh(bool)
#include <iostream>
#include <string>
#include <fstream>

#include "chrono/physics/ChSystemNSC.h"
#include "chrono/core/ChBezierCurve.h"
#include "chrono_vehicle/terrain/CRGTerrain.h"
#include "chrono_vsg/ChVisualSystemVSG.h"

using namespace chrono;
using namespace chrono::vehicle;

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
    const bool use_mesh = (argc > 2) ? (std::stoi(argv[2]) != 0) : true;

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
    std::cout << "    闭合 = " << (terrain.IsPathClosed() ? "是" : "否") << std::endl;

    // ---- 导出（不需要 GL，headless 也能出东西）
    terrain.ExportMeshWavefront("chrono_export");
    std::cout << "    已导出网格 -> chrono_export/ (Wavefront .obj)" << std::endl;
    DumpCurve(terrain.GetRoadCenterLine(),    "chrono_centerline.csv", "中心线");
    DumpCurve(terrain.GetRoadBoundaryLeft(),  "chrono_left.csv",       "左边界");
    DumpCurve(terrain.GetRoadBoundaryRight(), "chrono_right.csv",      "右边界");

    // ---- 可视化窗口
    ChVisualSystemVSG vis;
    vis.AttachSystem(&sys);
    vis.SetWindowSize(ChVector2i(1600, 900));
    vis.SetWindowTitle("2025Y095 - CRG road (Chrono::Vehicle)");
    vis.SetCameraVertical(CameraVerticalDir::Z);
    vis.AddCamera(ChVector3d(-120, -260, 120), ChVector3d(0, 0, 0));
    vis.EnableAbsFrameCoords();
    vis.Initialize();

    std::cout << "\n==> 窗口已开。鼠标左键=旋转 右键=平移 滚轮=缩放 Q/ESC=退出\n" << std::endl;

    while (vis.Run()) {
        vis.BeginScene();
        vis.Render();
        vis.EndScene();
    }
    return 0;
}
