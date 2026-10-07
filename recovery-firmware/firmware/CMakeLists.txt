cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wired_macro_pad)

if(NOT DEFINED WMP_BUILD_ID)
    set(WMP_BUILD_ID "custom-local-unidentified")
endif()
idf_build_set_property(
    COMPILE_DEFINITIONS
    "WMP_BUILD_ID=\"${WMP_BUILD_ID}\""
    APPEND
)
