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
// ★ 2026-10-09：为了 SetChronoDataPath()。见下面 main 开头的说明。
#include "chrono/core/ChDataPath.h"

// CHRONO_DATA_DIR 由 CMakeLists.txt 传进来（官方惯用法）。
// 宁可编译期报错，也不要运行期静默失败。
#ifndef CHRONO_DATA_DIR
#error "CHRONO_DATA_DIR 未定义：CMakeLists.txt 需有 target_compile_definitions(... \"CHRONO_DATA_DIR=\\\"${CHRONO_DATA_DIR}\\\"\")"
#endif
#include "chrono_vehicle/terrain/CRGTerrain.h"
#include "chrono_vsg/ChVisualSystemVSG.h"

// ★ 2026-10-09：为了在 Initialize() 之后把远裁剪面改掉。
//   ChVisualSystemVSG 没有 GetCamera()，但 GetRenderCommandGraph() 是公开的，
//   顺着它就能摸到 vsg::View，进而拿到相机。理由见 main 里「远裁剪面」那段。
#include <algorithm>
#include <vsg/app/CommandGraph.h>
#include <vsg/app/View.h>
#include <vsg/app/ProjectionMatrix.h>
#include <vsg/nodes/Group.h>

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
    // ★★★ 2026-10-09：必须先设 Chrono 数据目录，否则可视化**静默失败并段错误**。
    //
    //   症状：跑到开窗那步 SIGSEGV（退出码 139）。
    //   根因：chrono/core/ChDataPath.cpp 里那句
    //           static std::string chrono_data_path("../data/");
    //        是个**硬编码的相对路径**，没有任何东西会自动改它。
    //        ChVisualSystemVSG 构造时做
    //           m_options->paths.push_back(GetChronoDataPath());
    //        于是搜索路径里多出一条 "../data/"；Initialize() 再拿它去读
    //           vsg/fonts/OpenSans-Bold.vsgb
    //        那是相对**当前工作目录**的，读不到，于是打印
    //           Failed to read font : vsg/fonts/OpenSans-Bold.vsgb
    //        然后**直接 return** —— 此时窗口和 viewer 都还没建，m_viewer 是空指针。
    //        而 Run() 的实现就是
    //           bool ChVisualSystemVSG::Run() { return m_viewer->active(); }
    //        → 空指针解引用 → 段错误。
    //
    //   ★ 我原先把 "Failed to read font" 当成"无害且不可避免的警告"写进了 README。
    //     它不无害 —— 它直接终止了初始化。又一次栽在"被我当噪音的那一行"。
    //
    //   官方惯用法（chrono/template_project/CMakeLists.txt:107）：
    //       target_compile_definitions(my_demo PRIVATE "CHRONO_DATA_DIR=\"${CHRONO_DATA_DIR}\"")
    //   CMake 侧已照此把 CHRONO_DATA_DIR 传进来（绝对路径）。
    //   沙箱双向验证：设之前 GetChronoDataPath()="../data/"、字体读失败；
    //                设之后为绝对路径、字体读成功。
    {
        // ★★ 2026-10-09：**结尾斜杠不能丢。**
        //   GetChronoDataFile(f) 的实现是纯字符串拼接：
        //       return chrono_data_path + filename;
        //   所以 chrono_data_path 必须以 '/' 结尾。
        //
        //   我第一版写成
        //       SetChronoDataPath(std::filesystem::absolute(CHRONO_DATA_DIR, ec).lexically_normal().string());
        //   lexically_normal() 把 ".../share/chrono/data/" 规范成 ".../share/chrono/data"
        //   —— 末尾斜杠没了。于是 GetChronoDataFile("logo_chrono_alpha.png") 拼出
        //       .../share/chrono/datalogo_chrono_alpha.png   ← 不存在
        //   ChMainGuiVSG 构造时读不到 logo → vsgImGui::Texture::create_if 返回空
        //   → m_logo_texture 为空 → 紧接着 compile() 里
        //       m_app->m_logo_texture->compile(context);
        //   解引用空指针 → 又一次段错误（反汇编：mov 0x5c0(%rax),%rdi 得到 0）。
        //
        //   ★ 更值得记的是：我当时的"字体就位"自检**通过了**，因为我用的是
        //     std::filesystem::path(...) / "vsg/fonts/..."  —— operator/ 会自动补斜杠，
        //     而真正出事的那条路径用的是字符串拼接。
        //     又一次：**用另一种方法验证了被测代码**。教训是自检必须走真实路径。
        std::string data_dir = CHRONO_DATA_DIR;
        if (data_dir.empty() || data_dir.back() != '/')
            data_dir += '/';
        SetChronoDataPath(data_dir);
        std::cout << "==> Chrono 数据目录: " << data_dir << std::endl;
        std::cout << "    logo 实测路径: " << GetChronoDataFile("logo_chrono_alpha.png")
                  << std::endl;
    }

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
    //   实测（读 OpenCRG 自己的 NOTICE）：起点 phi = 2.6087 rad = 149.5°，
    //   终点 phi = 2.0462 rad = 117.2°，**相差 32.2°**。32.2 < 60，于是
    //   divisor = cos(32.2°) = 0.845 > 0.5，判成"可能闭合"，并给出
    //   uCloseMin = -2260.653、uCloseMax = 8066.053 —— 意思是"这条路若
    //   向前后各延长 2260 m 就会闭合成环"，纯属猜测。任何一条长而缓弯
    //   （总转角 < 60°）的路都会中这个招。
    //   ★ 这里原先写的是"158° 到 156°，只差 2°"，那是拿 1000 m 弦长当
    //     航向代理算出来的，**是错的**。真值来自 OpenCRG 自己的 NOTICE。
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
    // ★ 2026-10-09：开窗之前先确认字体在。理由见 main 开头 ——
    //   字体读不到时 Initialize() 会提前 return，留下空 viewer，
    //   随后 Run() 段错误。现场离原因很远，这里把它变成一句人话。
    {
        // ★ 两道自检都必须走**真实代码路径**：
        //   字体那处 Chrono 用的是 vsg::findFile(相对名, m_options)，options->paths 里
        //   就有 GetChronoDataPath()；logo 那处用的是 GetChronoDataFile() 字符串拼接。
        //   我上一版只查了字体，而且用的是 operator/ —— 于是"通过"了，
        //   可字符串拼接那条路已经断了。这次两条都查。
        const std::string font_path = GetChronoDataFile("vsg/fonts/OpenSans-Bold.vsgb");
        const std::string logo_path = GetChronoDataFile("logo_chrono_alpha.png");
        std::cout << "==> 字体: " << font_path << std::endl;
        std::cout << "==> logo: " << logo_path << std::endl;

        bool ok = true;
        if (!std::filesystem::exists(font_path)) {
            std::cout << "!! 字体不存在。ChVisualSystemVSG::Initialize 会打印\n"
                      << "   \"Failed to read font\" 并提前 return，留下空 viewer，\n"
                      << "   随后 Run() 解引用空指针 → 段错误。" << std::endl;
            ok = false;
        }
        if (!std::filesystem::exists(logo_path)) {
            std::cout << "!! logo 不存在。ChMainGuiVSG 构造时读不到它，\n"
                      << "   m_logo_texture 会是空指针，compile() 里\n"
                      << "   m_app->m_logo_texture->compile(context) → 段错误。\n"
                      << "   ★ 常见原因：SetChronoDataPath 的路径结尾少了 '/'，\n"
                      << "     GetChronoDataFile 是纯字符串拼接。" << std::endl;
            ok = false;
        }
        if (!ok) {
            std::cout << "   网格与 CSV 都已写出，本次到此为止。" << std::endl;
            return 1;
        }
    }

    // ---- 相机取景：按路的实际范围算，不写死坐标
    // ★ 2026-10-09：原来写的是 AddCamera((-120,-260,120), (0,0,0)) ——
    //   那是瞄着路的**起点**、距离只有 306 m。5.8 km 的路在窗口里只剩开头一小截。
    ChVector3d lo(1e30, 1e30, 1e30), hi(-1e30, -1e30, -1e30);
    if (auto cl = terrain.GetRoadCenterLine()) {
        for (const auto& p : cl->GetPoints()) {
            lo.x() = std::min(lo.x(), p.x());  hi.x() = std::max(hi.x(), p.x());
            lo.y() = std::min(lo.y(), p.y());  hi.y() = std::max(hi.y(), p.y());
            lo.z() = std::min(lo.z(), p.z());  hi.z() = std::max(hi.z(), p.z());
        }
    }
    const ChVector3d center(0.5 * (lo.x() + hi.x()),
                            0.5 * (lo.y() + hi.y()),
                            0.5 * (lo.z() + hi.z()));
    const double span = std::max(std::max(hi.x() - lo.x(), hi.y() - lo.y()), 1.0);
    // 60° 垂直视场下，装下 span 需要约 0.87*span 的距离；留约 1.7 倍余量。
    const double dist = 1.5 * span;
    const ChVector3d eye(center.x() - 0.55 * dist,
                         center.y() - 0.70 * dist,
                         center.z() + 0.45 * dist);
    std::cout << "\n==> 路面范围 X[" << lo.x() << ", " << hi.x() << "]  Y["
              << lo.y() << ", " << hi.y() << "]  跨度 " << span << " m" << std::endl;
    std::cout << "==> 相机 眼(" << eye.x() << ", " << eye.y() << ", " << eye.z()
              << ") -> 目标(" << center.x() << ", " << center.y() << ", " << center.z()
              << ")  距离 " << (eye - center).Length() << " m" << std::endl;

    ChVisualSystemVSG vis;
    vis.AttachSystem(&sys);
    vis.SetWindowSize(ChVector2i(1600, 900));
    vis.SetWindowTitle("2025Y095 - CRG road (Chrono::Vehicle)");
    vis.SetCameraVertical(CameraVerticalDir::Z);
    vis.AddCamera(eye, center);
    // 显示世界原点坐标系，便于判断路面的绝对方位。
    // 默认是关的（ChVisualSystemVSG.cpp:322  m_show_abs_frame(false)），
    // 所以 Toggle 一次即打开。它只有切换形式，没有显式 bool 重载。
    vis.SetAbsFrameScale(50.0);
    vis.ToggleAbsFrameVisibility();
    vis.Initialize();

    // ---- 远裁剪面
    // ★★ 2026-10-09：**这才是"怎么感觉只有一小段圆曲线"的真正原因。**
    //   Chrono 把远裁剪面写死成 500 m，而且没有任何接口能改：
    //     chrono_vsg/ChVisualSystemVSG.cpp:709
    //         double radius = 50.0;
    //     chrono_vsg/ChVisualSystemVSG.cpp:843-845
    //         vsg::Perspective::create(m_camera_angle_deg, aspect,
    //                                  nearFarRatio * radius,  // 0.001*50 = 0.05 m
    //                                  radius * 10.0);         // 50*10    = 500 m
    //   紧挨着的 L710 `vsg::dbox bound;` 声明了却**一次都没被用过** ——
    //   本该由场景包围盒去算 radius 的代码没写。上游硬伤，不是我们的 bug。
    //   后果：5.8 km 的路在 500 m 处被整段裁掉，视野里只剩起点附近一小片。
    //   官方 demo_VEH_CRGTerrain_VSG 不受影响 —— 它是跟车视角，只看前方百米。
    //
    //   绕法：ChVisualSystemVSG 没有 GetCamera()，但 GetRenderCommandGraph()
    //   是公开的；vsg::createRenderGraphForView()（VSG 源码 RenderGraph.cpp:221）
    //   会挂一个 View::create(camera)，而 vsg::View::camera 指的正是 Chrono
    //   自己持有的那个 Camera 对象。投影矩阵在 RecordTraversal.cpp:613
    //   每次录制命令图时重新读取，所以只改 vsg::Perspective 的两个 public
    //   字段就生效，lookAt / 轨迹球完全不受影响。
    {
        int hits = 0;
        // ★ vsg::ref_ptr 不是 std::shared_ptr，VSG 也没有提供自己的
        //   dynamic_pointer_cast，所以只能在裸指针上 dynamic_cast。
        //   （std::dynamic_pointer_cast<vsg::View>(ref_ptr) 会报
        //    "no matching function"，因为 ref_ptr 不继承 std::__shared_ptr。）
        auto walk = [&](auto&& self, vsg::ref_ptr<vsg::Node> n) -> void {
            if (!n) return;
            if (auto* v = dynamic_cast<vsg::View*>(n.get())) {
                if (v->camera && v->camera->projectionMatrix) {
                    if (auto* p = dynamic_cast<vsg::Perspective*>(
                            v->camera->projectionMatrix.get())) {
                        std::cout << "    原裁剪面 near=" << p->nearDistance
                                  << " far=" << p->farDistance << std::endl;
                        p->nearDistance = 1.0;
                        p->farDistance = 100000.0;   // 100 km，装得下任何一条路
                        ++hits;
                    }
                }
            }
            if (auto* g = dynamic_cast<vsg::Group*>(n.get()))
                for (auto& c : g->children) self(self, c);
        };
        walk(walk, vis.GetRenderCommandGraph());
        if (hits > 0)
            std::cout << "==> 远裁剪面已改 near=1 far=100000 m（命中 " << hits
                      << " 个相机）" << std::endl;
        else
            std::cout << "!! 没在命令图里找到相机，远处仍会被裁掉 —— "
                         "VSG 的命令图结构可能变了。" << std::endl;
    }

    // ★ 这句原来是无条件打印的：段错误时它照样打，于是"窗口已开"成了一句假话。
    //   现在真的去问一次 viewer。Run() 就是 while 循环里那个调用，不多担风险。
    if (!vis.Run()) {
        std::cout << "\n!! viewer 未进入活动状态 —— 窗口没起来。" << std::endl;
        return 1;
    }
    std::cout << "\n==> 窗口已开。鼠标左键=旋转 右键=平移 滚轮=缩放 Q/ESC=退出\n" << std::endl;

    while (vis.Run()) {
        vis.BeginScene();
        vis.Render();
        vis.EndScene();
    }
    return 0;
}
