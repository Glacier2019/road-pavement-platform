// =============================================================================
// 2025Y095 · CRG 路面上的整车仿真（第 1 步后半段）
// -----------------------------------------------------------------------------
// 目标：让一辆车沿库里那条真实线形（33 段、5805.4 m）走完全程，并导出轮荷。
//       前作 03_visualize.cpp 只把「路面」画出来，没有车；本文件补上「车」。
//
// 用法：
//   ./demo_vehicle_on_crg <crg文件> [--speed 15] [--offset 0] [--headless]
//                          [--duration 600] [--step 0.002]
//     --speed    目标速度 m/s（默认 15 ≈ 54 km/h）—— 速度可设
//     --offset   相对中心线的横向偏移 m，正数=向左（默认 0）—— 轨迹可设
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

#include "chrono/physics/ChSystemSMC.h"
#include "chrono/core/ChDataPath.h"
#include "chrono/core/ChBezierCurve.h"
#include "chrono/assets/ChVisualShapeBox.h"

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
            loads.push_back(wl);
        }
    }
    return loads;
}

int main(int argc, char* argv[]) {
    // ---------------------------------------------------------------- 参数
    std::string crg_file;
    double target_speed = 15.0;   // m/s
    double offset = 0.0;          // m，正=左
    bool headless = false;
    double duration = 600.0;      // s
    double step = 0.002;          // s

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
        } else if (a == "--headless") {
            headless = true;
        } else if (crg_file.empty()) {
            crg_file = a;
        }
    }
    if (crg_file.empty()) {
        std::cout << "用法: " << argv[0]
                  << " <crg文件> [--speed 15] [--offset 0] [--headless]"
                     " [--duration 600] [--step 0.002]\n";
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
    terrain.SetContactFrictionCoefficient(0.8f);
    terrain.SetRoadsidePostDistance(50.0);
    terrain.Initialize(crg_file);

    const double road_length = terrain.GetLength();
    const double road_width = terrain.GetWidth();
    std::cout << "    路长 = " << road_length << " m   路宽 = " << road_width << " m"
              << "   闭合 = " << (terrain.IsPathClosed() ? "是" : "否") << std::endl;

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
        2.0);   // ramp_duration：再用 2 s 把油门上限拉到 100%
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
        vis->SetWindowSize(1400, 900);
        vis->SetChaseCamera(ChVector3d(0, 0, 1.75), 8.0, 1.0);
        vis->SetLightDirection(1.5 * CH_PI_2, CH_PI_4);
        vis->EnableShadows();
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

    while (sys.GetChTime() < duration) {
        const double time = sys.GetChTime();

        if (vis) {
            if (!vis->Run())
                break;
            vis->BeginScene();
            vis->Render();
            vis->EndScene();
        }

        // 进度与横向偏差
        double s = 0.0, lat = 0.0;
        QueryProgress(poly, vehicle.GetPos(), s, lat);
        max_lat = std::max(max_lat, lat);

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

    // ---------------------------------------------------------------- 报告
    double final_s = 0.0, final_lat = 0.0;
    QueryProgress(poly, vehicle.GetPos(), final_s, final_lat);

    std::cout << "\n================ 结果 ================" << std::endl;
    std::cout << "仿真时长      : " << sys.GetChTime() << " s" << std::endl;
    std::cout << "最终里程      : " << final_s << " m / " << road_length << " m" << std::endl;
    std::cout << "最大横向偏差  : " << max_lat << " m  （半路宽 " << 0.5 * road_width << " m）" << std::endl;
    std::cout << "是否到达终点  : " << (arrived ? "是" : "否") << std::endl;
    std::cout << "是否飞出路面  : " << (off_road ? "是" : "否") << std::endl;

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
