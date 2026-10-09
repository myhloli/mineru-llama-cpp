#pragma once
#include <algorithm>
#include <cctype>
#include <stdexcept>
#include <string>
#include <vector>

namespace mineru_llama_cpp {
struct BackendDevice {
    std::string backend;
    bool integrated;
    size_t index;
};

// 统一上游注册名；Metal 使用 MTL 而非用户配置中的 metal。
inline std::string canonical_backend_name(std::string name) {
    // 大小写归一化时使用无符号字符，避免非 ASCII 字节的未定义行为。
    std::transform(name.begin(), name.end(), name.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    return name == "mtl" ? "metal" : name;
}

// 独显优先；同等级只选择一个后端，保留该后端下的多设备能力。
inline std::vector<size_t> select_backend_devices(const std::vector<BackendDevice> & devices,
                                                const std::string & requested,
                                                bool force_cpu = false) {
    static const std::vector<std::string> priority = {"cuda", "sycl", "metal", "vulkan"};
    if (requested != "auto" && requested != "cpu" &&
        std::find(priority.begin(), priority.end(), requested) == priority.end())
        throw std::invalid_argument("MINERU_LLAMA_CPP_BACKEND must be auto, cpu, cuda, sycl, vulkan or metal");
    if (force_cpu || requested == "cpu") return {};
    for (bool integrated : {false, true}) {
        for (const auto & backend : priority) {
            if (requested != "auto" && requested != backend) continue;
            std::vector<size_t> result;
            for (const auto & device : devices)
                if (device.integrated == integrated && canonical_backend_name(device.backend) == backend)
                    result.push_back(device.index);
            if (!result.empty()) return result;
        }
    }
    if (requested != "auto")
        throw std::runtime_error("Requested " + requested + " backend is unavailable; check GPU drivers and runtime libraries");
    return {};
}
} // namespace mineru_llama_cpp
