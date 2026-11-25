cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wired_macro_pad)

# cJSON recurses once per nesting level and defaults to 1000 levels, which no
# task stack here can hold. Callers pre-check 32; this bounds any they miss.
idf_component_get_property(wmp_cjson_lib espressif__cjson COMPONENT_LIB)
target_compile_definitions(${wmp_cjson_lib} PRIVATE CJSON_NESTING_LIMIT=64)

if(NOT DEFINED WMP_BUILD_ID)
    set(WMP_BUILD_ID "custom-local-unidentified")
endif()
idf_build_set_property(
    COMPILE_DEFINITIONS
    "WMP_BUILD_ID=\"${WMP_BUILD_ID}\""
    APPEND
)
