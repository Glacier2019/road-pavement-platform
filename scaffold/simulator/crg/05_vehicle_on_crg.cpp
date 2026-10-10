// =============================================================================
// 2025Y095 · CRG 路面上的整车仿真（第 1 步后半段）
// -----------------------------------------------------------------------------
// 目标：让一辆车沿库里那条真实线形（33 段、5805.4 m）走完全程，并导出轮荷。
//       前作 03_visualize.cpp 只把「路面」画出来，没有车；本文件补上「车」。
//
// 用法：
//   ./demo_vehicle_on_crg <crg文件> [--speed 15] [--offset -1.75] [--headless]
//                          [--duration 600] [--step 0.002]
//     --speed    目标速度 m/s（默认 15 ≈ 54 km/h）—— 速度可设
//     --offset   相对路中线的横向偏移 m，正数=向左
//                （默认 -1.75 = 靠右行驶，右侧 3.5 m 车道的中心线）—— 轨迹可设
//     --headless 不开窗口，纯计算（批量扫参数时用）
//     --duration 最长仿真时长 s（默认 600）
//     --step     积分步长 s（默认 0.002）
//
// -----------------------------------------------------------------------------
// ★ 2026-10-09：两个「相对默认值 + 纯字符串拼接」的坑，本文件各踩过一次的教训
//
//   坑 A —— chrono::GetChronoDataFile()
//     src/chrono/core/ChDataPath.cpp:  static std::string chrono_data_path("../data/");
//     GetChronoDataFile(f) 的实现是 `return chrono_data_path + f;` —— **纯拼接**。
//     所以路径**必须以 '/' 结尾**。少了尾斜杠会去找 ".../share/chrono/datalogo_...png"，
//     读不到 → ChVisualSystemVSG 的 logo 纹理为空 → ChMainGuiVSG::compile()
//     解空指针 → 段错误（实测崩在 +27 偏移，rdi=0）。
//     另外：这个默认值**没有任何代码会自动改**，必须由应用调 SetChronoDataPath()。
//
//   坑 B —— chrono::vehicle::GetVehicleDataFile()   ← 同一个坑的第二份拷贝
//     src/chrono_vehicle/ChVehicleDataPath.cpp:25
//       static std::string chrono_vehicle_data_path("../data/vehicle/");
//     GetVehicleDataFile(f) 同样是 `return chrono_vehicle_data_path + f;`。
//     而且**全仓库没有任何地方设置它**（没有静态初始化接 CHRONO_DATA_DIR，
//     也没有别的文件调 SetVehicleDataPath）。不设 → 车型 JSON 读不到。
//     本文件的做法：**从 CHRONO_DATA_DIR 推导**，而不是再写死一个路径。
//
//   ★ 通用教训：路径拼接型 API 与 operator/ 型 API 的行为不同。
//     `path(GetChronoDataPath()) / "vsg/fonts/x"` 会自己补分隔符，检查能过；
//     而真正出问题的那条路走的是字符串拼接。**用与被测代码不同的方式去验证，
//     等于没验证**——这是我上次把「字体就位」判为通过、却仍然段错误的原因。
// =============================================================================

#include <iostream>
#include <string>
#include <vector>
#include <cmath>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <cstdio>
#include <system_error>

#include "chrono/physics/ChSystemSMC.h"
#include "chrono/core/ChDataPath.h"
#include "chrono/core/ChBezierCurve.h"
#include "chrono/assets/ChVisualShapeBox.h"
#include "chrono/assets/ChVisualShapeTriangleMesh.h"
#include "chrono/geometry/ChTriangleMeshConnected.h"
#include <chrono>

#include "chrono_vehicle/ChVehicleDataPath.h"
#include "chrono_vehicle/ChWorldFrame.h"
#include "chrono_vehicle/ChSubsysDefs.h"
#include "chrono_vehicle/driver/ChPathFollowerDriver.h"
#include "chrono_vehicle/terrain/CRGTerrain.h"
#include "chrono_vehicle/wheeled_vehicle/ChWheeledVehicle.h"
#include "chrono_vehicle/wheeled_vehicle/ChAxle.h"
#include "chrono_vehicle/wheeled_vehicle/ChWheel.h"
#include "chrono_vehicle/wheeled_vehicle/ChTire.h"
// ★ 用这个头，不是 chrono_vsg/ChVisualSystemVSG.h —— 后者里没有
//   ChWheeledVehicleVisualSystemVSG（它只提供裸的 ChVisualSystemVSG）。
#include "chrono_vehicle/wheeled_vehicle/ChWheeledVehicleVisualSystemVSG.h"

#include "chrono_models/vehicle/hmmwv/HMMWV.h"

// 数据目录由 CMake 从 ChronoConfig.cmake 的 CHRONO_DATA_DIR 注入。
// 缺了就直接编译失败 —— 宁可编不过，也不要运行时静默找错路径。
#ifndef CHRONO_DATA_DIR
#error "CHRONO_DATA_DIR 未定义：请检查 CMakeLists.txt 里的 target_compile_definitions"
#endif

using namespace chrono;
using namespace chrono::vehicle;

// -----------------------------------------------------------------------------
// 把 CRG 中心线按横向偏移平移，得到一条新的 Bezier 路径。
//
// 为什么需要它：CRGTerrain **只提供三条线** —— 中线（v=0）和左右边界：
//   GetRoadCenterLine() / GetRoadBoundaryLeft() / GetRoadBoundaryRight()
// 没有「给个 v 偏移取一条线」的接口。而 WIM 断面（K4640+000）的传感器埋在
// **行车道 2 的轮迹带**上，不在中线。所以要自己在法向平移。
//
// ISO 世界坐标系（Z 上、X 前、Y 左）下，切线 (tx,ty) 的**左法向**是 (-ty, tx)：
//   切线 (1,0) → 左法向 (0,1) = +Y ✓
// 因此 offset > 0 表示向**左**偏移。
// -----------------------------------------------------------------------------
static std::shared_ptr<ChBezierCurve> BuildOffsetPath(const std::shared_ptr<ChBezierCurve>& src,
                                                      double offset) {
    const std::vector<ChVector3d> pts = src->GetPoints();
    const size_t n = pts.size();
    if (offset == 0.0 || n < 3)
        return src;

    std::vector<ChVector3d> out;
    out.reserve(n);
    for (size_t i = 0; i < n; ++i) {
        const ChVector3d& a = pts[i == 0 ? 0 : i - 1];
        const ChVector3d& b = pts[i + 1 >= n ? n - 1 : i + 1];
        // 切线投影到水平面：路面坡度不该影响横向偏移的方向
        ChVector3d t(b.x() - a.x(), b.y() - a.y(), 0.0);
        if (t.Length() < 1e-9) {
            out.push_back(pts[i]);
            continue;
        }
        t.Normalize();
        // 左法向 (-ty, tx, 0)
        const ChVector3d nrm(-t.y(), t.x(), 0.0);
        out.push_back(pts[i] + offset * nrm);
    }
    return chrono_types::make_shared<ChBezierCurve>(out, false);
}

// -----------------------------------------------------------------------------
// 把路径离散成折线，供「进度」与「横向偏差」查询使用。
// 返回：折线点、累计里程。
// -----------------------------------------------------------------------------
struct Polyline {
    std::vector<ChVector3d> pts;
    std::vector<double> s;  // 累计里程，与 pts 等长
};

static Polyline SamplePath(const std::shared_ptr<ChBezierCurve>& path, int n_per_seg) {
    Polyline pl;
    const std::vector<ChVector3d> knots = path->GetPoints();
    if (knots.size() < 2)
        return pl;
    const int segs = static_cast<int>(knots.size()) - 1;
    for (int i = 0; i < segs; ++i) {
        const int last = (i == segs - 1) ? n_per_seg : n_per_seg - 1;
        for (int j = 0; j <= last; ++j) {
            const double u = static_cast<double>(j) / n_per_seg;
            pl.pts.push_back(path->Eval(i, u));
        }
    }
    pl.s.resize(pl.pts.size(), 0.0);
    for (size_t i = 1; i < pl.pts.size(); ++i)
        pl.s[i] = pl.s[i - 1] + (pl.pts[i] - pl.pts[i - 1]).Length();
    return pl;
}

// 查询点 p 在折线上的 (里程, 横向偏差)。横向偏差取水平面内的最近距离。
static void QueryProgress(const Polyline& pl, const ChVector3d& p, double& s_out, double& lat_out) {
    s_out = 0.0;
    lat_out = 1e30;
    for (size_t i = 1; i < pl.pts.size(); ++i) {
        const ChVector3d a = pl.pts[i - 1];
        const ChVector3d b = pl.pts[i];
        ChVector3d ab(b.x() - a.x(), b.y() - a.y(), 0.0);
        const double L2 = ab.Length2();
        if (L2 < 1e-12)
            continue;
        ChVector3d ap(p.x() - a.x(), p.y() - a.y(), 0.0);
        double t = ap.Dot(ab) / L2;
        t = std::max(0.0, std::min(1.0, t));
        const ChVector3d proj(a.x() + t * ab.x(), a.y() + t * ab.y(), 0.0);
        const double d = std::sqrt((p.x() - proj.x()) * (p.x() - proj.x()) +
                                   (p.y() - proj.y()) * (p.y() - proj.y()));
        if (d < lat_out) {
            lat_out = d;
            s_out = pl.s[i - 1] + t * (pl.s[i] - pl.s[i - 1]);
        }
    }
}

// -----------------------------------------------------------------------------
// 读一辆车的所有轮荷。TerrainForce::force 是**全局坐标系**下的力；
// ISO 世界坐标系（Z 上）下，垂向载荷就是 force.z()。
//
// ★ 用 ReportTireForce(terrain) 而不是 GetTireForce()：
//   ChTire::GetTireForce() 是 **protected**（头文件里在 protected 段），
//   外部调不到。公开的那两个是
//       ReportTireForce(ChTerrain*)            → 全局坐标系
//       ReportTireForceLocal(ChTerrain*, frame) → 轮胎坐标系
//   所以要拿 ChTerrain*。**这就是它要一个 terrain 参数的原因。**
// -----------------------------------------------------------------------------
struct WheelLoad {
    int axle;
    bool left;
    double vertical;
    double lateral;
    double longitudinal;
    double contact_z;   // 着地点高程；force 为 0 时用它区分「零载荷」与「没有数据」
};

static std::vector<WheelLoad> ReadWheelLoads(ChWheeledVehicle& veh, ChTerrain* terrain) {
    std::vector<WheelLoad> loads;
    const auto& axles = veh.GetAxles();
    for (size_t ai = 0; ai < axles.size(); ++ai) {
        const auto& wheels = axles[ai]->GetWheels();
        for (size_t wi = 0; wi < wheels.size(); ++wi) {
            const auto& w = wheels[wi];
            if (!w->GetTire())
                continue;
            const TerrainForce tf = w->GetTire()->ReportTireForce(terrain);
            WheelLoad wl;
            wl.axle = static_cast<int>(ai);
            // ChAxle::GetWheels() 的顺序是「先左后右」（单胎轴即 left, right）
            wl.left = (wi % 2 == 0);
            wl.vertical = tf.force.z();
            wl.lateral = tf.force.y();
            wl.longitudinal = tf.force.x();
            wl.contact_z = tf.point.z();
            loads.push_back(wl);
        }
    }
    return loads;
}

// -----------------------------------------------------------------------------
// 轮荷时间序列导出
// -----------------------------------------------------------------------------
// ★ 为什么必须有它：轮荷是**随时间变化的量**，只在结束时打一帧快照不算"出轮荷"。
//   动载系数、轮荷谱、冲击系数，全都要看时间序列 —— 一帧快照一个都给不出来。
//   这也是 Chrono 路线图 step 1 的最后一句「出轮荷/侧向力」的落点。
//
// 单位用 kN（Chrono 是 SI 的 N），因为下游看的都是 kN 量级。
// 列名带轴号与左右，是为了让文件自己能说明自己，不必回头读代码。
//
// ★★ 为什么还有一列 pz_m（着地点高程）—— 用来区分两种"零"：
//   Chrono 的 TerrainForce 在**没有接触**时是默认构造的，force 和 point 同时为
//   零向量。于是 Fz=0 有两种完全不同的含义：
//       · 轮胎真的离地了     → 没有载荷
//       · 轮胎受力恰为零     → 有载荷，值为 0
//   只看 Fz 分不出来，而这两种情况对 WIM 是天壤之别（一个该丢弃，一个该记录）。
//   实测判据：路面高程在 54~84 m 之间，**真实的着地点不可能落在 z=0**。
//   所以 `Fz=0 且 pz=0` ⇒ 无接触；`Fz=0 且 pz>0` ⇒ 真零载荷（本次运行未出现）。
//   ★ 顺带这一列本身就是有用的数据：每个轮子脚下的路面高程。
//   ★ 通用教训：**一个"零"如果可能表示"没有数据"，它就不能长得像"数值为零"。**
//     必须让两者在数据里可区分，否则下游一定会误读。
// -----------------------------------------------------------------------------
class WheelLoadCsv {
  public:
    WheelLoadCsv(const std::string& path, double dt) : m_dt(dt) {
        m_out.open(path);
        m_ok = m_out.is_open();
    }
    bool ok() const { return m_ok; }
    double dt() const { return m_dt; }

    void Write(double t, double s, double lat, double speed, double chassis_z,
               const std::vector<WheelLoad>& loads) {
        if (!m_ok)
            return;
        if (!m_header_done) {
            m_out << "t_s,s_m,lat_m,speed_mps,chassis_z_m";
            for (const auto& w : loads)
                for (const char* q : {"Fz_kN", "Fy_kN", "Fx_kN", "pz_m"})
                    m_out << ",a" << w.axle << (w.left ? "_L_" : "_R_") << q;
            m_out << "\n";
            m_header_done = true;
        }
        m_out << t << ',' << s << ',' << lat << ',' << speed << ',' << chassis_z;
        for (const auto& w : loads)
            m_out << ',' << w.vertical / 1000.0 << ',' << w.lateral / 1000.0 << ','
                  << w.longitudinal / 1000.0 << ',' << w.contact_z;
        m_out << "\n";
        ++m_rows;
    }
    long rows() const { return m_rows; }

  private:
    std::ofstream m_out;
    bool m_ok = false;
    bool m_header_done = false;
    double m_dt = 0.05;
    long m_rows = 0;
};

int main(int argc, char* argv[]) {
    // ---------------------------------------------------------------- 参数
    std::string crg_file;
    double target_speed = 15.0;   // m/s
    // ★★★ 默认**靠右行驶**，不是压中线。★★★
    //   这是二级公路，横断面直接从设计表里读得到（roadbed_design_point，
    //   section_id = 6）：
    //     左车道 3.5 + 右车道 3.5 + 左硬路肩 0.75 + 右硬路肩 0.75 = 8.5 m
    //     （左/右中央分隔带均为 0，加减速车道 extra_width_09/11 均为 0）
    //   8.5 m 正好等于 CRG 的 v 覆盖宽度，反过来印证了这张横断面表。
    //   右侧车道中心距路中线 3.5 / 2 = 1.75 m；
    //   而 offset **正 = 左**（BuildOffsetPath 取左法向 (-ty, tx)，
    //   见函数头注释），所以靠右就是 **-1.75**。
    //   ★ 默认值就是"对的那一个"：不加 --offset 时也应当合法上路。
    //     压着中线跑虽然动力学上完全正常，但几何上它是**对向车道**，
    //     WIM 断面（K4640+000 行车道 2 轮迹带）也不在线上。
    double offset = -1.75;        // m，正=左；默认 = 右侧车道中心
    bool headless = false;
    double duration = 600.0;      // s
    double step = 0.002;          // s
    std::string csv_path;         // 空 = 不导出
    double csv_dt = 0.05;         // s，导出采样间隔
    std::string video_dir;        // 空 = 不抓帧；否则把 PNG 写进这个目录
    double video_dt = 0.2;        // s，抓帧间隔（仿真时间）
    int video_w = 1280, video_h = 720;   // 视频分辨率
    double render_dt = 0.0;       // s，渲染节流；0 = 不节流（每步都渲染）
    bool use_texture = true;      // 路面贴纹理（--no-texture 关）
    bool use_sky = true;          // 天空穹顶（--no-sky 关）
    bool use_shadows = true;      // 硬阴影（--no-shadows 关）
    bool use_markings = true;     // 车道标线（--no-markings 关）
    int  pbr = 0;                 // 0=只漫反射  1=+法线  2=+法线+粗糙度

    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&](double& dst) {
            if (i + 1 < argc)
                dst = std::stod(argv[++i]);
        };
        if (a == "--speed") {
            next(target_speed);
        } else if (a == "--offset") {
            next(offset);
        } else if (a == "--duration") {
            next(duration);
        } else if (a == "--step") {
            next(step);
        } else if (a == "--csv-dt") {
            next(csv_dt);
        } else if (a == "--csv") {
            if (i + 1 < argc)
                csv_path = argv[++i];
        } else if (a == "--video-dt") {
            next(video_dt);
        } else if (a == "--render-dt") {
            next(render_dt);
        } else if (a == "--no-texture") {
            use_texture = false;
        } else if (a == "--no-sky") {
            use_sky = false;
        } else if (a == "--no-markings") {
            use_markings = false;
        } else if (a == "--no-shadows") {
            use_shadows = false;
        } else if (a == "--pbr") {
            if (i + 1 < argc)
                pbr = std::stoi(argv[++i]);
        } else if (a == "--video-size") {
            if (i + 1 < argc) {
                const std::string wh = argv[++i];
                const auto x = wh.find('x');
                if (x != std::string::npos) {
                    video_w = std::stoi(wh.substr(0, x));
                    video_h = std::stoi(wh.substr(x + 1));
                }
            }
        } else if (a == "--video") {
            if (i + 1 < argc)
                video_dir = argv[++i];
        } else if (a == "--headless") {
            headless = true;
        } else if (crg_file.empty()) {
            crg_file = a;
        }
    }
    if (crg_file.empty()) {
        std::cout << "用法: " << argv[0]
                  << " <crg文件> [--speed 15] [--offset -1.75] [--headless]"
                     " [--duration 600] [--step 0.002]"
                     " [--csv 轮荷.csv] [--csv-dt 0.05]"
                     " [--video 帧目录] [--video-dt 0.2] [--video-size 1280x720]"
                     " [--render-dt 0.1] [--no-texture] [--no-sky] [--no-shadows] [--no-markings] [--pbr 0|1|2]\n";
        return 2;
    }
    // --video 与 --headless 互斥：headless 根本不建窗口，没有帧可抓。
    // 这种自相矛盾的参数必须当场拒绝，而不是跑完 400 s 才发现一个文件都没写。
    if (!video_dir.empty() && headless) {
        std::cerr << "\n!!! --video 需要窗口，--headless 不建窗口，两者不能同时给。" << std::endl;
        return 2;
    }

    // ------------------------------------------------- 数据目录（两个坑）
    // 坑 A：Chrono 主数据目录。**必须带尾斜杠**（GetChronoDataFile 是纯拼接）。
    std::string data_dir = CHRONO_DATA_DIR;
    if (data_dir.empty() || data_dir.back() != '/')
        data_dir += '/';
    SetChronoDataPath(data_dir);

    // 坑 B：车辆数据目录。**必须设置，且必须以 '/' 结尾**（同样是纯拼接）。
    //
    // ★★★ 2026-10-10 实测：**不能直接信 Chrono 导出的 CHRONO_VEHICLE_DATA_DIR。** ★★★
    //   ChronoTargets.cmake:83 确实带了这条定义，但它的值是
    //       INTERFACE_COMPILE_DEFINITIONS "CHRONO_VEHICLE_DATA_DIR=\"${_IMPORT_PREFIX}/data/vehicle/\""
    //   注意是 <前缀>/data/vehicle/，**不是** <前缀>/share/chrono/data/vehicle/。
    //   本机实测：
    //       /home/zhanghe/Packages/chrono/data/vehicle       -> 不存在
    //       /home/zhanghe/Packages/chrono/share/chrono/data/vehicle -> 存在
    //   也就是说**上游导出的那条路径是错的**。而这个错误是静默的：宏有定义、
    //   值也非空，GetVehicleDataFile() 只是拼出一个不存在的路径，
    //   然后 HMMWV 的 JSON 读不到 —— 报错点离病根十万八千里。
    //   更别扭的是：你在 CMakeLists 里覆盖它，编译器只会给一句
    //   `warning: 'CHRONO_VEHICLE_DATA_DIR' redefined` —— **唯一的提示是警告，
    //   而错误本身是致命的**。
    //   所以本文件用**自己的宏名** WIM_CHRONO_VEHICLE_DATA_DIR（由 CMakeLists 注入
    //   正确路径），优先用它；退路依次是上游的 CHRONO_VEHICLE_DATA_DIR，
    //   再退到「CHRONO_DATA_DIR + vehicle/」。
#if defined(WIM_CHRONO_VEHICLE_DATA_DIR)
    std::string veh_dir = WIM_CHRONO_VEHICLE_DATA_DIR;
#elif defined(CHRONO_VEHICLE_DATA_DIR)
    std::string veh_dir = CHRONO_VEHICLE_DATA_DIR;
#else
    std::string veh_dir = data_dir + "vehicle/";
#endif
    if (veh_dir.empty() || veh_dir.back() != '/')
        veh_dir += '/';
    SetVehicleDataPath(veh_dir);

    std::cout << "==> Chrono 数据目录: " << GetChronoDataPath() << std::endl;
    std::cout << "==> 车辆数据目录:   " << GetVehicleDataPath() << std::endl;
    std::cout << "    logo 实测路径:  " << GetChronoDataFile("logo_chrono_alpha.png") << std::endl;

    // 前置条件：目录不存在就别往下走。带着错路径跑下去只会得到一个难查的崩溃。
    for (const auto& p : {std::filesystem::path(data_dir), std::filesystem::path(veh_dir)}) {
        if (!std::filesystem::exists(p)) {
            std::cout << "!! 目录不存在: " << p << std::endl;
            return 1;
        }
    }
    const std::string hmmwv_json = veh_dir + "hmmwv/chassis/HMMWV_Chassis.json";
    if (!std::filesystem::exists(hmmwv_json)) {
        std::cout << "!! 车型 JSON 不存在: " << hmmwv_json << std::endl;
        std::cout << "   —— 这几乎总是 SetVehicleDataPath 少写或多写了斜杠。" << std::endl;
        return 1;
    }

    // ---------------------------------------------------------------- 系统
    ChSystemSMC sys;
    sys.SetCollisionSystemType(ChCollisionSystem::Type::BULLET);
    sys.SetGravitationalAcceleration(-9.81 * ChWorldFrame::Vertical());
    sys.SetSolverType(ChSolver::Type::BARZILAIBORWEIN);
    sys.GetSolver()->AsIterative()->SetMaxIterations(150);
    sys.SetMaxPenetrationRecoverySpeed(4.0);

    // ---------------------------------------------------------------- 地形
    // ★ 官方 demo 的注释（demo_VEH_CRGTerrain_VSG.cpp:273）：
    //   "For a crg terrain with arbitrary start heading the terrain class must be
    //    initialized before the vehicle class"
    //   我们的路起始方位角是 149.5°，属于 arbitrary start heading，必须照做。
    std::cout << "\n==> CRG 文件: " << crg_file << std::endl;
    CRGTerrain terrain(&sys);
    terrain.UseMeshVisualization(true);
    // ★★ 视觉网格简化：CRGTerrain::SimplifyMesh(true) 把 v 通道从
    //    「按 m_vinc 等分」换成一张固定的 5 条表（vbeg, -0.05, 0, 0.05, vend），
    //    见 CRGTerrain.cpp:159-163。我们这个 CRG 有 86 条 v 通道，
    //    简化后三角面数除以 17。
    //    ★ 它只改**画出来的网格**；物理仍走 crgEvaluv2z 的解析查询，
    //      轮荷、轨迹一个字都不变。
    //    头文件对不简化的那支的原话就是「Default: show original mesh, maybe slow」——
    //    实测 1280x720 软件渲染下约 13 s/帧，不简化根本录不成视频。
    if (!video_dir.empty() || render_dt > 0.0) {
        terrain.SimplifyMesh(true);
        std::cout << "==> 视觉网格已简化（v 通道 86 → 5，只为出图；物理不受影响）" << std::endl;
    }
    // ★★ 路面纹理。不加纹理时，CRG 地形是一张**平涂灰色网格**：
    //    靠近的路面邻接三角面法向几乎相同 → 渲染出来就是一整片均匀灰，
    //    追随相机看 0.5 s（15 m/s 走 7.5 m）画面几乎不变。
    //    实测：整帧平均像素差只有 0.0002/255，全图 45%~56% 是同一个背景色。
    //    贴一张混凝土漫反射图之后，路面自己带着纹理在动，视频才有信息量。
    //    ★ 这条必须放在 Initialize() 之前 —— 纹理是在 Initialize →
    //      SetupMeshGraphics() 里挂到 visual shape 上的（CRGTerrain.cpp:543-561），
    //      之后再设文件名不会生效。
    if (use_texture) {
        const std::string tex_dir = "vehicle/terrain/textures/Concrete002_2K-JPG/";
        terrain.SetRoadDiffuseTextureFile(tex_dir + "Concrete002_2K_Color.jpg");
        if (pbr >= 1)
            terrain.SetRoadNormalTextureFile(tex_dir + "Concrete002_2K_NormalGL.jpg");
        if (pbr >= 2)
            terrain.SetRoadRoughnessTextureFile(tex_dir + "Concrete002_2K_Roughness.jpg");
        std::cout << "==> 路面纹理已挂（Concrete002，漫反射"
                  << (pbr >= 1 ? " + 法线" : "") << (pbr >= 2 ? " + 粗糙度" : "") << "）"
                  << std::endl;
    }
    terrain.SetContactFrictionCoefficient(0.8f);
    terrain.SetRoadsidePostDistance(50.0);
    terrain.Initialize(crg_file);

    const double road_length = terrain.GetLength();
    const double road_width = terrain.GetWidth();

    // ---------------------------------------------------------------- 车道标线
    // ★ 二级公路的标准横断面（数字直接来自设计表 roadbed_design_point，
    //   section_id = 6 的 332 个断面全都是这一组值）：
    //       左硬路肩 0.75 | 左车道 3.50 | 右车道 3.50 | 右硬路肩 0.75
    //       左/右中央分隔带 0.00，加减速车道 extra_width_09/11 均为 0.00
    //   合计 8.5 m —— 与 CRG 报的 v 覆盖宽度一致，互为印证。
    //   于是标线的位置是算出来的，不是挑出来的：
    //       v =  0.00  → 路中线（黄色虚线）
    //       v = ±3.50  → 行车道边缘线（白色实线，正好是车道与硬路肩的分界）
    //       v = -1.75  → 右侧车道中心 = 本程序的默认行驶位置
    //   为什么非加不可：路面只有一张混凝土贴图时，8.5 m 宽的路面是一整片
    //   **无参照的灰**，车在哪条车道上根本看不出来 —— 而"靠右行驶"恰恰
    //   是必须一眼可辨的事。中心线用**虚线**还有第二个好处：
    //   它本身就带运动视差，能让人确认车在往前走。
    if (use_markings) {
        // ★★ 标线必须建立在**修复后**的中线上。★★
        //   CRGTerrain::m_isClosed 是个纯几何猜测（首末航向差 < 60°），
        //   对这条开放的 5805 m 山路**误判为闭合**，于是 GetRoadCenterLine()
        //   的末控制点被首点覆盖，曲线里凭空多出一段约 8.7 km 的跳变
        //   （折线总长 14499.8 m vs 路长 5805.4 m）。
        //   跳过变段去问地形高程时，点落在 CRG 网格之外，
        //   crgEvalxy2uv 在那里反复迭代不收敛 —— 第一版就是这样把整个
        //   程序拖到 5 分钟超时（exit 124、0 帧）的。
        //   ★ 注意这不是"GetHeight 太贵"：实测它 0.0084 ms/次
        //     （≈12 万次/秒），2.9 万次只要 0.24 s。成本猜错了两次，
        //     最后靠的是实测。
        //   判据与下面主路径那处修复完全一致（> 1.2 × 路长 才算误闭合）。
        auto center_curve = terrain.GetRoadCenterLine();
        {
            Polyline probe = SamplePath(center_curve, 8);
            if (!probe.s.empty() && probe.s.back() > 1.2 * road_length) {
                std::vector<ChVector3d> cpts = center_curve->GetPoints();
                cpts.pop_back();
                center_curve = chrono_types::make_shared<ChBezierCurve>(cpts, false);
            }
        }

        auto MakeStripe = [&](double v_center, double width, double dash_period,
                              double dash_len, float r, float g, float b) {
            auto mesh = chrono_types::make_shared<ChTriangleMeshConnected>();
            std::vector<ChVector3d>& V = mesh->GetCoordsVertices();
            std::vector<ChVector3i>& F = mesh->GetIndicesVertices();

            // 沿中线按弧长取断面（SamplePath 已按弧长重采样，间距约 0.375 m）
            Polyline pl = SamplePath(center_curve, 8);
            const int M = static_cast<int>(pl.pts.size());
            const double STEP = 3.0;   // 每 3 m 一个断面，够贴 255 m 半径的圆曲线

            // 高程逐顶点问地形。GetHeight 走 crgEvalxy2uv（逆变换、迭代搜索），
            // 我一度认定它太贵 —— **实测 0.0084 ms/次**，2.9 万次总共 0.24 s。
            // "逐顶点反查把程序算死"是错的；真凶是上面那段跳变曲线。
            // ★ 顺带一条：中线控制点自己的 z 与地形实测差了约 0.2 m，
            //   所以**不能**拿中线 z 当路面高程用，必须问地形（见下面的自检）。
            //

            auto put = [&](int i, double dv) {
                const int i0 = (i == 0) ? 0 : i - 1;
                const int i1 = (i + 1 >= M) ? M - 1 : i + 1;
                ChVector3d t(pl.pts[i1].x() - pl.pts[i0].x(),
                             pl.pts[i1].y() - pl.pts[i0].y(), 0.0);
                if (t.Length() < 1e-9)
                    t = ChVector3d(1, 0, 0);
                t.Normalize();
                const ChVector3d nrm(-t.y(), t.x(), 0.0);   // 左法向，与 BuildOffsetPath 同式
                ChVector3d q = pl.pts[i] + (v_center + dv) * nrm;
                // 高程问地形实测，再抬 2 cm 防 z-fighting
                q.z() = terrain.GetHeight(q) + 0.02;
                return q;
            };

            // 沿弧长每 STEP 米取一个断面。
            // ★ 虚线遇到空隙时必须把 prev 清掉，否则下一段会跨过空隙
            //   跟上一段连成一个大四边形 —— 虚线会被"填实"，
            //   而且看起来像画对了。
            int prev_a = -1, prev_b = -1;
            double next_s = -1.0;
            for (int i = 0; i < M; ++i) {
                if (pl.s[i] + 1e-9 < next_s)
                    continue;
                next_s = pl.s[i] + STEP;

                if (dash_period > 0.0 &&
                    std::fmod(pl.s[i], dash_period) >= dash_len) {
                    prev_a = prev_b = -1;      // ← 空隙：断开
                    continue;
                }

                const ChVector3d A = put(i, +0.5 * width);
                const ChVector3d B = put(i, -0.5 * width);
                const int a1 = static_cast<int>(V.size()); V.push_back(A);
                const int b1 = static_cast<int>(V.size()); V.push_back(B);
                if (prev_a >= 0) {
                    // 绕序 (A0,B0,A1) 与 (B0,B1,A1) 的叉积都是 +z
                    //（已手算核对），所以法线朝上，无需自己填法线：
                    // ChShapeBuilderVSG.cpp:552-559 在法线索引缺失时会
                    // 自己用叉积算面法线。
                    F.push_back(ChVector3i(prev_a, prev_b, a1));
                    F.push_back(ChVector3i(prev_b, b1, a1));
                }
                prev_a = a1; prev_b = b1;
            }

            auto shape = chrono_types::make_shared<ChVisualShapeTriangleMesh>();
            shape->SetMesh(mesh);
            shape->SetColor(ChColor(r, g, b));
            return shape;
        };

        auto mark_body = chrono_types::make_shared<ChBody>();
        mark_body->SetFixed(true);
        mark_body->EnableCollision(false);          // 纯视觉，不参与动力学
        sys.Add(mark_body);
        mark_body->AddVisualShape(MakeStripe( 0.00, 0.15, 6.0, 4.0, 0.95f, 0.80f, 0.10f));
        mark_body->AddVisualShape(MakeStripe( 3.50, 0.15, 0.0, 0.0, 0.92f, 0.92f, 0.92f));
        mark_body->AddVisualShape(MakeStripe(-3.50, 0.15, 0.0, 0.0, 0.92f, 0.92f, 0.92f));
        std::cout << "==> 车道标线已加（黄虚线中线 v=0；白实线车道边缘 v=±3.50；"
                  << "行车道 3.5 m + 硬路肩 0.75 m）" << std::endl;
        // ★ 标线高程自检。标线的 z 直接取自地形实测，所以这里只需要说清
        //   两件事，都是我原先猜错、后来量出来的：
        //     (1) 中线控制点自己的 z 与地形实测**差约 0.2 m** ⇒ 不能拿它当
        //         路面高程用（标线因此走 GetHeight，不受影响）。
        //     (2) 真实路拱高差有多大 —— 我曾凭 elev_diff 表推出"2.53% 横坡"，
        //         那是错的；横坡小到可以忽略，路面近乎水平。
        //   代价：抽 24 断面 × 3 点 = 72 次 GetHeight ≈ 0.6 ms。
        {
            Polyline chk = SamplePath(center_curve, 8);
            const int MC = static_cast<int>(chk.pts.size());
            auto nrm_at = [&](int i) {
                const int i0 = (i == 0) ? 0 : i - 1;
                const int i1 = (i + 1 >= MC) ? MC - 1 : i + 1;
                ChVector3d t(chk.pts[i1].x() - chk.pts[i0].x(),
                             chk.pts[i1].y() - chk.pts[i0].y(), 0.0);
                t.Normalize();
                return ChVector3d(-t.y(), t.x(), 0.0);
            };
            double worst = 0.0, crown = 0.0;
            int tested = 0;
            for (int k = 0; k < 24; ++k) {
                const int i = (MC - 1) * k / 23;
                const ChVector3d n = nrm_at(i);
                const double zc = terrain.GetHeight(chk.pts[i]);
                worst = std::max(worst, std::fabs(zc - chk.pts[i].z()));
                for (double vv : {3.50, -3.50}) {
                    ChVector3d q = chk.pts[i] + vv * n;
                    crown = std::max(crown, std::fabs(terrain.GetHeight(q) - zc));
                    ++tested;
                }
            }
            std::cout << "    标线高程自检：抽 " << tested << " 点；路拱高差最大 "
                      << crown * 1000.0 << " mm；中线控制点 z 与地形实测最大差 "
                      << worst * 1000.0 << " mm（标线取实测值，不受其影响）"
                      << std::endl;
        }
    }
    std::cout << "    路长 = " << road_length << " m   路宽 = " << road_width << " m"
              << "   闭合 = " << (terrain.IsPathClosed() ? "是" : "否") << std::endl;
    if (use_texture) {
        // 纹理重复次数 = CRGTerrain::SetupMeshGraphics() 里 SetTextureScale() 的
        // scale_u，算法是 0.5 * 路长 / 路宽（CRGTerrain.cpp:551-553）。
        // ★ 这段必须放在 Initialize() **之后**：Initialize() 之前 CRG 还没读，
        //   GetLength()/GetWidth() 都返回 0，算出来是 0/0 = NaN，
        //   转成 int 就是 INT_MIN —— 我第一版就打印出了「重复 -2147483648 次」。
        //   ★ 一个荒唐到刺眼的数字救了我；如果当初打印的是"每 0 m 一个循环"
        //     这种看着合理的值，这个错误会一直留着。
        const double tex_repeat = 0.5 * road_length / road_width;
        std::cout << "    路面纹理重复 " << static_cast<int>(tex_repeat) << " 次（每 "
                  << road_length / tex_repeat << " m 一个循环）" << std::endl;
    }

    // ---------------------------------------------------------------- 路径
    //
    // ★★ 2026-10-09 实测踩坑：**不能直接拿 GetRoadCenterLine() 当行驶轨迹。**
    //
    //   CRGTerrain::GetRoadCenterLine() 结尾有这么一段（CRGTerrain.cpp:344 起）：
    //       if (m_isClosed) { pathpoints.back() = pathpoints[0]; }
    //   而 m_isClosed 是**几何启发式猜的**（首末方位角差 < 60° 即判闭合）。
    //   本段路线实测首方位角 149.465°、末 117.239°，差 32.2° → 被判成闭合，
    //   于是最后一个控制点被首点**覆盖**，曲线里凭空多出一条
    //   从 u≈5805 跳回 u=0 的贝塞尔段。
    //
    //   后果（实测数字，不是推测）：折线长 14499.8 m，而真实路长 5805.4 m ——
    //   多出来的 8694 m 就是那条跳变段。车明明停在起点，
    //   QueryProgress 却投影到跳变段上返回 s = 14499.8，
    //   于是**第一步就判定「到达终点」**，全程只跑了 0.002 s，
    //   轮荷还全是 0（车都没落稳），而退出码是 0、结论是「通过」。
    //   这是本项目最危险的一类失败：**不报错，还报成功。**
    //
    //   修法：丢掉被覆盖的末点，用剩下的控制点重建一条**开放**曲线。
    //   丢掉的末点在 u ≈ 5802.4 m 处（离真实终点约 3 m），可接受。
    auto center = terrain.GetRoadCenterLine();
    auto path = BuildOffsetPath(center, offset);
    Polyline poly = SamplePath(path, 8);
    double poly_len = poly.s.empty() ? 0.0 : poly.s.back();

    // ★ 误闭合的判别放在**量出折线长之后**，因为「首末点距离」根本查不出来：
    //   GetRoadCenterLine() 已经把 back() 覆盖成 front()，
    //   所以 (front - back).Length() 恒等于 0。
    //   我第一版就是拿这个当条件 —— 检查写了、条件恒假、
    //   修复一次都没执行，而输出看上去"有在检查"。**恒假的检查比没有检查更坏。**
    //   唯一量得出来的证据是折线总长：跳变段会把 5805 m 撑到 14499 m。
    if (poly_len > 1.2 * road_length && terrain.IsPathClosed()) {
        std::cout << "==> 折线长 " << poly_len << " m ≫ 地形路长 " << road_length
                  << " m，且地形判为闭合 → 判为**误闭合**"
                  << "（末控制点已被首点覆盖），丢掉末点重建开放曲线" << std::endl;
        std::vector<ChVector3d> cpts = center->GetPoints();
        cpts.pop_back();
        center = chrono_types::make_shared<ChBezierCurve>(cpts, false);
        path = BuildOffsetPath(center, offset);
        poly = SamplePath(path, 8);
        poly_len = poly.s.empty() ? 0.0 : poly.s.back();
    }

    std::cout << "==> 目标速度 = " << target_speed << " m/s ("
              << target_speed * 3.6 << " km/h)"
              << "   横向偏移 = " << offset << " m" << std::endl;
    std::cout << "    路径控制点 = " << path->GetPoints().size()
              << "   折线采样 = " << poly.pts.size()
              << "   折线长 = " << poly_len << " m" << std::endl;

    // ★ 自检：折线长必须和地形报的路长对得上。
    //   上面那个坑的特征正是「折线长 ≫ 路长」，而且**不报任何错**，
    //   只会让仿真一步跑完然后宣布成功。宁可在这里硬失败。
    if (poly_len > 1.2 * road_length || poly_len < 0.8 * road_length) {
        std::cerr << "\n!!! 路径自检失败：折线长 " << poly_len
                  << " m 与地形路长 " << road_length
                  << " m 相差超过 20%。轨迹不可信，拒绝继续。" << std::endl;
        return 2;
    }

    // ---------------------------------------------------------------- 车辆
    ChCoordsys<> init_csys = terrain.GetStartPosition();

    // ★★★ 坑八：不能把车放在 u=0 的起点上。★★★
    //
    //   CRG 只在 u∈[0, 路长] 上有数据，crgEvalxy2uv 对范围外的 xy 会把 u 钳到边界。
    //   车放在起点时，后轴的轮子落在 u<0 的外面：
    //       轮心 xy = (1.649, 0.910)  ->  GetPoint 返回 xy = (-1.882, 0.054)
    //   注意 z 还是对的（都被钳到 u=0），所以 GetHeight 看不出异常；
    //   错的是**平面上那个点的 xy**。法向只要有极小的水平分量
    //   （实测 n=(-0.00519, 0, 0.99999)），平面在轮子真实 xy 处的高度就会偏
    //   18 mm，凭空造出 7 mm 的假穿透：
    //       depth = r - |(A-P)·n| = 0.4699 - 0.462976 = 0.0069  （实测 0.006895）
    //   这 7 mm 假穿透被 TMeasy 当成真力，四轮合力把 2.4 t 的车弹到 4 m 高空。
    //   所以起点必须沿路径**往前挪**一段，让所有轮子都落在路面覆盖内。
    const double start_offset_m = 10.0;
    {
        size_t k = 0;
        while (k + 1 < poly.s.size() && poly.s[k] < start_offset_m) ++k;
        init_csys.pos.x() = poly.pts[k].x();
        init_csys.pos.y() = poly.pts[k].y();

        // ★★★ 坑九：朝向必须取自**路径切线**，不能用 terrain.GetStartPosition().rot。★★★
        //   实测 GetStartPosition() 给的朝向与路径方向差了 149.4 度（正好是路的起始方位角），
        //   等价于一个不转向的朝向。车按它起步，5 s 内纯 +X 冲出路面 4.25 m：
        //       t=1.0 xy=(-8.65993, 5.14659) -> t=5.0 xy=(-2.56994, 5.14997)
        //       Δ=(+6.156, +0.003)  而路径方向是 (-0.861, +0.508)
        //   从路径切线取朝向，车才顺着路走，转向控制器也才有东西可纠。
        {
            ChVector3d tng(poly.pts[k + 1] - poly.pts[k]);
            tng.z() = 0.0;
            const double heading = std::atan2(tng.y(), tng.x());
            init_csys.rot = ChQuaternion<>(std::cos(0.5 * heading), 0.0, 0.0, std::sin(0.5 * heading));
            std::cout << "==> 起点朝向取自路径切线: " << heading * 180.0 / CH_PI << " 度" << std::endl;
        }

        std::cout << "==> 起点沿路径前移 " << poly.s[k] << " m（避开 u=0 边界钳位）"
                  << "  xy=(" << init_csys.pos.x() << ", " << init_csys.pos.y() << ")" << std::endl;
    }

    const double road_z0 = terrain.GetHeight(init_csys.pos);

    // ★★★ 坑七：出生高度必须"零穿透"，否则整条仿真从第一帧就废了。★★★
    //
    //   官方 demo 写的是 `init_csys.pos += 0.5 * Vertical()` —— 一个拍脑袋的常数。
    //   对通用车型它够用；对 HMMWV 不行：实测这样摆出来的轮底在路面**下方 4.8 mm**。
    //
    //   4.8 mm 看着无所谓，但 TMeasy 的法向刚度约 810 kN/m
    //   （实测：穿 23 mm -> 18.8 kN/轮），静载只需要 6 kN/轮。
    //   于是初始垂向力是静载的 3 倍，四轮合计 75 kN 而车重 24 kN：
    //       t=0.10 s 力涨到 31 kN -> 车被抛到 3.9 m 高空
    //       t=0.85 s 落地砸出 72 kN 尖峰 -> 一步扎穿路面
    //       t=0.95 s 起 轮心落到路面下方，DiscTerrainCollision1pt 的单边判据
    //                `if (ChWorldFrame::Height(C) <= h) return false;`
    //                永久判无接触 -> 无限下坠（实测 1.15 s 已掉到 53.5 m）
    //
    //   所以这里不猜：先在**一次性系统**里建一台同样的车量出轮底相对底盘的真实几何，
    //   再按 "路面高度 + 间隙 - 轮底偏置" 反算起点。换车型也自动成立。
    double wheel_bottom_offset = 0.0;   // 轮底 z 相对"底盘初始化点 z"的偏置
    {
        ChSystemSMC probe;
        hmmwv::HMMWV_Full probe_model(&probe);
        probe_model.SetContactMethod(ChContactMethod::SMC);
        probe_model.SetChassisCollisionType(CollisionType::NONE);
        probe_model.SetTireType(TireModelType::TMEASY);
        probe_model.SetInitPosition(ChCoordsys<>(ChVector3d(0, 0, 0), QUNIT));
        probe_model.Initialize();
        auto& pv = probe_model.GetVehicle();
        double zmin = 1e30;
        for (auto& ax : pv.GetAxles())
            for (auto& wh : ax->GetWheels())
                zmin = std::min(zmin, wh->GetPos().z() - wh->GetTire()->GetRadius());
        wheel_bottom_offset = zmin;
    }
    const double spawn_clearance = 0.02;   // 出生时轮底离路面留 2 cm，落地不砸
    init_csys.pos.z() = road_z0 + spawn_clearance - wheel_bottom_offset;
    std::cout << "==> 出生高度自校准: 路面 z=" << road_z0
              << "  轮底偏置=" << wheel_bottom_offset
              << " m  间隙=" << spawn_clearance
              << " m  => 底盘初始 z=" << init_csys.pos.z() << std::endl;

    // 若做了横向偏移，起点也要跟着挪，否则车会从路外开始找路
    if (offset != 0.0 && poly.pts.size() > 1) {
        ChVector3d t(poly.pts[1].x() - poly.pts[0].x(), poly.pts[1].y() - poly.pts[0].y(), 0.0);
        if (t.Length() > 1e-9) {
            t.Normalize();
            const ChVector3d nrm(-t.y(), t.x(), 0.0);
            init_csys.pos += offset * nrm;
        }
    }

    // ★ HMMWV_Full 在 chrono::vehicle::hmmwv 里，不是 chrono::vehicle 下。
    //   头文件 HMMWV.h 的命名空间是 chrono::vehicle::hmmwv（L32-34）。
    hmmwv::HMMWV_Full vehicle_model(&sys);
    vehicle_model.SetContactMethod(ChContactMethod::SMC);
    vehicle_model.SetChassisCollisionType(CollisionType::NONE);

    // ★★★ 必须显式换掉轮胎模型，否则整条仿真从第一帧起就是错的。★★★
    //
    //   HMMWV 的默认轮胎是 **刚性轮胎**（HMMWV.cpp:66 与 :90 两处构造函数都写着
    //   m_tireType(TireModelType::RIGID)），而刚性轮胎靠**刚体碰撞**拿力：
    //   ChRigidTire.cpp:53 就是 wheel_body->EnableCollision(true)。
    //
    //   偏偏 CRGTerrain 从设计上就没有碰撞体 —— CRGTerrain.cpp:74 明写
    //   m_ground->EnableCollision(false)，整个文件里一次 AddCollisionShape 都没有。
    //   它是 ChTerrain 子类，路面只以**解析式高度查询**的形式存在。
    //
    //   两者一撞：轮胎找不到任何可碰撞的物体，**一点力都不产生**，
    //   车从 57.76 m 自由落体，12 s 掉到 -536 m，轮荷全程恒为 0，
    //   而程序不报任何错。实测（诊断输出）：
    //       地面刚体 = 有   碰撞模型 = 无   碰撞形状数 = 0   碰撞体是否启用 = 否
    //       质心 z=52.87 -> 13.64 -> -64.90 -> -182.80 -> -339.92 -> -536.36
    //
    //   换成半经验轮胎就对了：ChTMeasyTire::Synchronize(time, const ChTerrain&)
    //   走的是 DiscTerrainCollision(...)，**解析式查地形**，压根不需要碰撞体。
    //   这也正是官方 demo 能跑的原因 —— 它用的是通用车型模型，
    //   而通用车型默认就是半经验轮胎。
    //
    //   教训：**"默认值"是一份没人读的契约。** 组件 A 关掉碰撞、组件 B 默认靠碰撞，
    //   两边各自的文档都没错，拼起来就是个静默失效。
    vehicle_model.SetTireType(TireModelType::TMEASY);

    vehicle_model.SetInitPosition(init_csys);
    vehicle_model.Initialize();

    auto& vehicle = vehicle_model.GetVehicle();

    // 轮胎换没换掉，用 typeid 查，不看注释
    {
        auto tire = vehicle.GetAxle(0)->GetWheel(LEFT)->GetTire();
        std::cout << "==> 轮胎类型 = " << typeid(*tire).name()
                  << "   静半径 = " << tire->GetRadius() << " m" << std::endl;
    }
    std::cout << "==> 车辆已装: HMMWV_Full   轴数 = " << vehicle.GetNumberAxles()
              << "   质心 = " << vehicle.GetPos() << std::endl;

    // ★★ 开车辆可视化。**Chrono 9 把这事的默认值改成了 NONE**，
    //    于是 HMMWV 在画面里是**完全隐形的** —— 而它并不是没有模型：
    //    HMMWV_Chassis.cpp:72 早就写好了 m_geometry.vis_model_file =
    //    "hmmwv/hmmwv_chassis.obj"，HMMWV_Wheel.cpp:38 写了 hmmwv_rim.obj，
    //    HMMWV_TMeasyTire 写了 hmmwv_tire_left/right.obj。
    //    只是那份几何要有人**主动物化**：ChRigidChassis::AddVisualizationAssets()
    //    会调 m_geometry.CreateVisualizationAssets(m_body, vis)
    //    （ChRigidChassis.cpp:67-72），而它的上游开关就是下面这几句。
    //    ★ ChWheeledVehicle 上**没有**一个总的 SetVisualizationType，
    //      必须按部件分别设（ChVehicle.h:254 + ChWheeledVehicle.h:137-153）。
    //      只设底盘不设轮子，车会像一块"浮在路上的板子"。
    //    ★ 不放这几句的后果不是报错，是**画面里没有车**：
    //      追随相机一直对着一段空路，你只会以为"视频没动"。
    vehicle.SetChassisVisualizationType(VisualizationType::MESH);
    vehicle.SetWheelVisualizationType(VisualizationType::MESH);
    vehicle.SetTireVisualizationType(VisualizationType::MESH);
    std::cout << "==> 车辆可视化已开（底盘 / 轮辋 / 轮胎 = MESH）" << std::endl;

    // ---------------------------------------------------------------- 驾驶
    // ★ 必须给目标速度加**斜坡**。ChPathFollowerDriver 的速度环是
    //   throttle = Kp * (目标 - 实际)，静止起步时误差 15 m/s、Kp=0.4 => 6.0，
    //   被钳到满油门。AWD 四轮从零转速硬吃满扭矩 => 轮胎滑移率爆炸 =>
    //   TMeasy 的松弛长度内部状态发散（实测 t=0.3 s 报出 713.8 kN 的垂向力，
    //   轮心被甩到 89.7 m 而底盘停在 59 m，悬挂直接撕开）。
    //   构造器的第 5/6 个参数就是干这个的：先保持 0 一段时间，再线性升到目标。
    auto driver = chrono_types::make_shared<ChPathFollowerDriver>(
        vehicle, path, "wim_path", target_speed,
        0.5,    // zero_duration：0.5 s 内目标速度保持 0，让车先落稳
        2.0);   // ramp_duration：见下
    // ★★ 注意 ramp_duration 这个名字是**骗人的**。它不是"速度斜坡"，
    //   而是在这段时间里把**油门和转向一起**按 t/ramp_duration 线性缩放
    //   （ChPathFollowerDriver.cpp:113-121，m_throttle *= alpha; m_steering *= alpha;）。
    //   也就是说它同时压住两个通道的上限，作用类似"暖机"。
    //   读名字以为在调速度曲线，实际上会连带把转向也压小 —— 起步阶段车
    //   转不过弯，可能就是这个原因。真正调速度用构造器第 4 个参数或 SetDesiredSpeed。
    //   ★ 通用教训：**参数的语义在实现里，不在名字里。**
    // 速度环用官方 demo 的增益（demo_VEH_CRGTerrain_VSG.cpp:108）
    driver->GetSpeedController().SetGains(0.4, 0, 0);
    // ★★★ 坑十：转向环的增益**默认全是 0**，必须自己设。★★★
    //   ChSteeringController.cpp:109
    //       ChPathSteeringControllerPID::ChPathSteeringControllerPID(path)
    //           : ChSteeringController(path), m_Kp(0), m_Ki(0), m_Kd(0) {}
    //   头文件 L159 也写着 "The user is responsible for calling SetGains
    //   and SetLookAheadDistance"。不设的后果是**静默**的：转向输出恒为 0，
    //   车不报警、不报错，只是直着开，遇到弯道就滑出路面
    //   （实测 28 s / 329 m 处横向偏差 4.25 m 出界）。
    //   下面这两个数照抄官方 demo_VEH_CRGTerrain_VSG.cpp:113-114。
    driver->GetSteeringController().SetLookAheadDistance(5.0);
    driver->GetSteeringController().SetGains(0.5, 0, 0);
    driver->Initialize();

    // ---------------------------------------------------------------- 可视化
    std::shared_ptr<ChWheeledVehicleVisualSystemVSG> vis;
    if (!headless) {
        vis = chrono_types::make_shared<ChWheeledVehicleVisualSystemVSG>();
        vis->SetWindowTitle("2025Y095 - vehicle on CRG road");
        vis->SetWindowSize(video_dir.empty() ? 1400 : video_w,
                           video_dir.empty() ? 900 : video_h);
        vis->SetChaseCamera(ChVector3d(0, 0, 1.75), 8.0, 1.0);
        vis->SetLightDirection(1.5 * CH_PI_2, CH_PI_4);
        if (use_shadows)
            vis->EnableShadows();
        // ★ 天空穹顶。默认背景是一块纯色（实测占画面 45%~56%），
        //   换成天空贴图后画面才有远近参照，车跑起来才看得出在动。
        //   ★ 必须在 Initialize() 之前调用（头文件原话：This function must be
        //     called before Initialize()）。
        if (use_sky)
            vis->EnableSkyTexture(SkyMode::DOME);
        vis->AttachVehicle(&vehicle);
        vis->AttachTerrain(&terrain);
        vis->Initialize();
    }

    // ---------------------------------------------------------------- 主循环
    std::cout << "\n==> 开始仿真（步长 " << step << " s，最长 " << duration << " s）\n" << std::endl;

    // ★ 步进前的几何自检：每个轮子的轮底必须**在路面上方**。
    //   轮心若一开始就在路面下方，ChTire::DiscTerrainCollision1pt 的
    //   `if (ChWorldFrame::Height(C) <= h) return false;` 会让接触判定
    //   永远为假，车不是"落地弹一下"，而是静默无限下坠。
    {
        double worst = 1e30;
        for (int ai = 0; ai < vehicle.GetNumberAxles(); ++ai) {
            auto ax = vehicle.GetAxle(ai);
            for (int si = 0; si < 2; ++si) {
                auto wh = ax->GetWheel(si == 0 ? LEFT : RIGHT);
                const ChVector3d wc = wh->GetPos();
                const double gap = wc.z() - wh->GetTire()->GetRadius() - terrain.GetHeight(wc);
                worst = std::min(worst, gap);
            }
        }
        std::cout << "==> 出生几何自检: 最小轮底离地间隙 = " << worst << " m" << std::endl;
        if (worst < 0.0) {
            std::cerr << "\n!!! 出生几何自检失败：有轮子的轮底已在路面之下（"
                      << worst << " m）。接触判定会永远为假，车会静默下坠。拒绝继续。"
                      << std::endl;
            return 2;
        }
    }

    double last_report = -1.0;
    bool off_road = false;
    bool arrived = false;
    double max_lat = 0.0;

    // 视频抓帧（--video）。★★ 三个必须讲清楚的点：
    //
    // 1) 为什么不能每步都渲染。
    //    本程序原本在循环里**每步**调 vis->Render()。无窗口时那是空操作，
    //    有窗口时就是每步真渲染一次。实测：软件渲染（lavapipe）下渲染一帧
    //    约 0.15 s，而不渲染的一步只要 1.8 ms —— 差 80 倍。全程 400 s 是
    //    20 万步，每步渲染要跑 8 小时。所以开视频时**只在抓帧的那几步渲染**。
    //
    // 2) 为什么抓的是"上一帧"。
    //    ChVisualSystemVSG::Render() 内部先 recordAndSubmit()，再检查
    //    m_capture_image，此时 ExportScreenImage() 取的是 imageIndex(1)，
    //    也就是**前一帧**的色缓冲（ChVisualSystemVSG.cpp:1200-1208）。
    //    官方 CRGTerrain demo 因此写成「先 BeginScene/Render，再
    //    WriteImageToFile」——落盘的其实是这次 Render 之前的那一帧。
    //    照抄这个顺序，文件与仿真时刻就是对齐的。
    //
    // 3) 为什么第一帧必须跳过。
    //    同上：第一帧没有"上一帧"可取。官方 demo 里留着一行注释
    //    「does not work with frame == 0!」（demo_VSG_assets.cpp:457）。
    //    这里用 rendered_so_far 计数，第一帧只渲染不写文件。
    long video_rendered = 0;   // 已经渲染过多少帧（用来跳过第 0 帧）
    long video_written = 0;    // 已经落盘多少张
    // 渲染节流间隔：--video 用 video_dt；否则用 --render-dt；都没有 = 每步渲染。
    // ★ 节流是给**外部录屏**用的：窗口按固定仿真时间间隔刷新，
    //   仿真本身全速跑。不走 Chrono 的 WriteImageToFile，因为那条路
    //   每抓一帧要新建 staging buffer/command pool/fence 并同步等设备，
    //   软件 Vulkan 下实测 13~18 s/帧，做不成视频。
    const double vis_dt = !video_dir.empty() ? video_dt : render_dt;
    double next_render_t = 0.0;
    if (!video_dir.empty()) {
        std::error_code ec;
        std::filesystem::create_directories(video_dir, ec);
        if (ec) {
            std::cerr << "\n!!! 建不了视频帧目录：" << video_dir << "（" << ec.message()
                      << "）拒绝继续" << std::endl;
            return 2;
        }
        std::cout << "==> 视频抓帧到 " << video_dir << "（每 " << video_dt << " s 一帧，"
                  << video_w << "x" << video_h << "）" << std::endl;
    }

    // 轮荷时间序列导出（--csv）。★ 在**进入循环前**就打开文件：
    // 写不进去要立刻知道，而不是跑完 400 s 才发现路径是错的。
    WheelLoadCsv csv(csv_path, csv_dt);
    double next_csv_t = 0.0;
    bool forces_valid = false;  // 轮胎模型是否已报出非零载荷（见循环里的采样条件）
    long csv_rows = 0;          // 已导出的行数
    long csv_airborne = 0;      // 其中有轮子离地的行数（Fz=0 且 pz=0）
    if (!csv_path.empty()) {
        if (!csv.ok()) {
            std::cerr << "\n!!! 打不开轮荷 CSV：" << csv_path << "（拒绝继续）" << std::endl;
            return 2;
        }
        std::cout << "==> 轮荷时间序列导出到 " << csv_path << "（每 " << csv_dt << " s 一行）"
                  << std::endl;
    }

    while (sys.GetChTime() < duration) {
        const double time = sys.GetChTime();

        // ★★ 只节流**渲染**，不节流 Run/Synchronize/Advance。
        //    (1) 省不到东西。节流前后同一个 20 s 仿真分别是 79.3 s 和 78.8 s
        //        —— 1 万步的 Run/Synchronize/Advance 加起来只占 0.5 s。
        //        真正贵的是 Render()，而 Render() 贵在**地形网格的面数**
        //        （见上面 SimplifyMesh 那一段）。起初我以为 71 s 全花在
        //        这三个函数的每步开销上，量完发现不是 —— 方向猜对了一半。
        //    (2) 节流会**弄坏相机**。ChVehicleVisualSystemVSG::Advance(step)
        //        内部是
        //            double t = 0;
        //            while (t < step) { h = min(m_stepsize, step - t);
        //                               m_camera->Update(h);  t += h; }
        //        也就是追随相机的**积分步**，隐含假设「每步都调一次，
        //        于是 Σstep = 已过时间」。按 --render-dt 节流后每 0.5 s
        //        只喂 0.002 s，相机只走正常速度的 0.4%，等于定住。
        //        实测证据：节流版的地平线从第 279 行一路爬到 547 行、
        //        7.5 s 后彻底不动；不节流版的地平线全程稳在 278~282 行。
        //        ★ 定住这一点骗过了我很久 —— 因为追随相机跑在一条路上，
        //          地平线**本来就该**固定在屏幕同一行。真正露馅的是
        //          "先爬 268 行再不动"这个**过程**。
        //    ★ 教训：**节流一个"每步调用"的回调之前，先量它值多少钱。**
        //      这次它一文不值，而节流它引入了一个静默的相机失效。
        const bool render_now = !vis || vis_dt <= 0.0 || time >= next_render_t;

        if (vis) {
            if (!vis->Run())
                break;
            if (render_now) {
                vis->BeginScene();
                vis->Render();
                vis->EndScene();
                if (vis_dt > 0.0)
                    next_render_t = time + vis_dt;
                if (!video_dir.empty()) {
                    // ★ 第一帧跳过：没有"上一帧"可抓。
                    if (video_rendered > 0) {
                        char buf[1024];
                        std::snprintf(buf, sizeof(buf), "%s/frame_%05ld.png",
                                      video_dir.c_str(), video_written);
                        vis->WriteImageToFile(buf);
                        ++video_written;
                    }
                    ++video_rendered;
                }
            }
        }

        // 进度与横向偏差
        double s = 0.0, lat = 0.0;
        QueryProgress(poly, vehicle.GetPos(), s, lat);
        max_lat = std::max(max_lat, lat);

        // ★ 采样点放在**判据之前**：这样"飞出路面"和"到达终点"这两帧也会被记下来。
        //   若放在判据之后，出问题的那一刻恰好是唯一没被记录的时刻。
        //
        // ★★ 起步那两帧的轮荷是 0，必须丢掉。这个坑我连踩三次，值得写清楚：
        //   轮胎力由 vehicle.Synchronize() 算出，而采样在它之前，所以采样永远
        //   落后一帧；而**第一帧 Synchronize(0) 本身就返回 0**（步长为 0，
        //   TMeasy 不给力）。t=0 的真实轮荷其实是整备重量 24 kN，不是 0。
        //   于是：
        //     · 「跳过 t=0」        → t=0.002 那帧还是 0
        //     · 「同步过就采」      → 同上，因为零力正是第一次同步的产物
        //     · 「推进过就采」      → 还是同上，力要到**下一次**同步才更新
        //   按时间或按调用次数猜都不对。唯一自洽的判据是**看数据本身**：
        //   等轮胎模型第一次报出非零载荷，此前一律不写。
        //   （这不掩盖真实事件：整车四轮同时为 0 只可能是起步那一瞬，
        //     真腾空时这个条件也早已为真，腾空帧照样会被记下来。）
        //
        //   ★ 判据是「**每个轮子**都非零」，不是「合计非零」。合计非零还不够：
        //     实测第一帧合计 16.08 kN，可它全来自前轴，后轴两个轮子都是 0。
        //     同一类毛病下沉了一层，判据就得跟着下沉一层。
        if (!csv_path.empty()) {
            const auto loads_now = ReadWheelLoads(vehicle, &terrain);
            bool all_nonzero = !loads_now.empty();
            for (const auto& w : loads_now)
                if (w.vertical == 0.0)
                    all_nonzero = false;
            if (all_nonzero)
                forces_valid = true;
            if (forces_valid && time >= next_csv_t) {
                csv.Write(time, s, lat, vehicle.GetSpeed(), vehicle.GetPos().z(), loads_now);
                next_csv_t += csv_dt;
                ++csv_rows;
                for (const auto& w : loads_now)
                    if (w.vertical == 0.0 && w.contact_z == 0.0) {
                        ++csv_airborne;
                        break;
                    }
            }
        }

        // ★ 「不飞出路面」判据：横向偏差超过半路宽即判飞出。
        //   这里用中心线到边界的距离，路宽 8.5 m → 半宽 4.25 m。
        if (lat > 0.5 * road_width) {
            std::cout << "!! t=" << time << " s 横向偏差 " << lat
                      << " m 超过半路宽 " << 0.5 * road_width << " m —— 车已飞出路面。" << std::endl;
            off_road = true;
            break;
        }

        // 每 5 s 报一次
        if (time - last_report >= 5.0) {
            last_report = time;
            std::cout << "    t=" << time << " s   里程=" << s << " m / " << road_length
                      << " m   横向偏差=" << lat << " m   车速=" << vehicle.GetSpeed()
                      << " m/s   底盘 z=" << vehicle.GetPos().z() << std::endl;
        }

        // 到达判据：里程接近全长
        if (s >= road_length - 5.0) {
            arrived = true;
            std::cout << "\n==> 已到达路的终点：里程 " << s << " m / " << road_length << " m" << std::endl;
            break;
        }

        driver->Synchronize(time);
        terrain.Synchronize(time);
        vehicle.Synchronize(time, driver->GetInputs(), terrain);

        if (vis)
            vis->Synchronize(time, driver->GetInputs());
        driver->Advance(step);
        terrain.Advance(step);
        vehicle.Advance(step);
        if (vis)
            vis->Advance(step);
        sys.DoStepDynamics(step);
    }

    // ★ 收尾补一次渲染。最后一次 WriteImageToFile 只是置了标志，真正的
    //   ExportScreenImage() 发生在**下一次** Render() 里。循环一结束就没有
    //   下一次了，不补这一下，最后一张就永远停在标志位里、文件不会出现。
    //   （这不是理论担忧：只渲染不补渲染，落盘张数会比预期少一张。）
    if (vis && !video_dir.empty()) {
        vis->BeginScene();
        vis->Render();
        vis->EndScene();
    }

    // ---------------------------------------------------------------- 报告
    double final_s = 0.0, final_lat = 0.0;
    QueryProgress(poly, vehicle.GetPos(), final_s, final_lat);

    std::cout << "\n================ 结果 ================" << std::endl;
    std::cout << "仿真时长      : " << sys.GetChTime() << " s" << std::endl;
    std::cout << "最终里程      : " << final_s << " m / " << road_length << " m" << std::endl;
    std::cout << "最大横向偏差  : " << max_lat << " m  （半路宽 " << 0.5 * road_width << " m）" << std::endl;
    std::cout << "是否到达终点  : " << (arrived ? "是" : "否") << std::endl;
    std::cout << "是否飞出路面  : " << (off_road ? "是" : "否") << std::endl;
    if (!csv_path.empty()) {
        std::cout << "轮荷时间序列  : " << csv.rows() << " 行 → " << csv_path << std::endl;
        std::cout << "  其中有轮子离地: " << csv_airborne << " 行（"
                  << (csv_rows ? 100.0 * csv_airborne / csv_rows : 0.0) << "%）"
                  << " —— 判据是 Fz=0 且 pz=0；不是零载荷，是**没有接触**" << std::endl;
    }

    if (!video_dir.empty()) {
        std::cout << "视频帧        : " << video_written << " 张 → " << video_dir << std::endl;
        std::cout << "  合成命令    : ffmpeg -framerate " << (1.0 / video_dt)
                  << " -i " << video_dir << "/frame_%05d.png"
                  << " -c:v libx264 -pix_fmt yuv420p -crf 20 输出.mp4" << std::endl;
        std::cout << "  （-framerate 取 1/video_dt 即为**实时**；调大就是快放）" << std::endl;
    }

    const auto loads = ReadWheelLoads(vehicle, &terrain);
    double total_v = 0.0;
    std::cout << "\n各轮垂向力（末态）:" << std::endl;
    for (const auto& w : loads) {
        total_v += w.vertical;
        std::cout << "    轴" << w.axle << (w.left ? " 左" : " 右")
                  << "  垂向 " << w.vertical / 1000.0 << " kN"
                  << "  侧向 " << w.lateral / 1000.0 << " kN"
                  << "  纵向 " << w.longitudinal / 1000.0 << " kN" << std::endl;
    }
    std::cout << "    合计垂向 " << total_v / 1000.0 << " kN"
              << "  （HMMWV 整备约 24 kN，偏差大说明有动载或姿态异常）" << std::endl;

    const bool ok = arrived && !off_road;
    std::cout << "\n判据：走完全程 且 不飞出路面  -> " << (ok ? "通过" : "未通过") << std::endl;
    return ok ? 0 : 1;
}
