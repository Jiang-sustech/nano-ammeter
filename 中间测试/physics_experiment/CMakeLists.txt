cmake_minimum_required(VERSION 3.22)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Debug")
endif()

set(CMAKE_PROJECT_NAME physics_experiment)
project(${CMAKE_PROJECT_NAME})

enable_language(C ASM)

# HAL 驱动与 CMSIS 头文件复用正式版工程 (仅头文件, 源码已在本地 Drivers/HAL)
# 目录布局有两种, 这里自动探测:
#   扁平布局 <...>/<本工程>/../Version1/Drivers/...
#   嵌套布局 <...>/<本工程>/../Version1/Version1/Drivers/...
set(V1_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../Version1")
if(EXISTS "${V1_DIR}/Version1/Drivers/STM32L4xx_HAL_Driver/Inc")
    set(V1_DIR "${V1_DIR}/Version1")
endif()

file(GLOB HAL_SRC "${CMAKE_CURRENT_SOURCE_DIR}/Drivers/HAL/*.c")

add_executable(${CMAKE_PROJECT_NAME}
    ${CMAKE_CURRENT_SOURCE_DIR}/startup_stm32l431xx.s
    ${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/main.c
    ${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/current.c
    ${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/gpio.c
    ${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/adc.c
    ${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/ads8866.c
    ${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/tim.c
    ${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/stm32l4xx_it.c
    ${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/system_stm32l4xx.c
    ${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/syscalls.c
    ${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/sysmem.c
    ${HAL_SRC}
)

target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/Core/Inc
    ${V1_DIR}/Drivers/STM32L4xx_HAL_Driver/Inc
    ${V1_DIR}/Drivers/STM32L4xx_HAL_Driver/Inc/Legacy
    ${V1_DIR}/Drivers/CMSIS/Device/ST/STM32L4xx/Include
    ${V1_DIR}/Drivers/CMSIS/Include
)

target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE
    DEBUG
    STM32L431xx
    USE_HAL_DRIVER
)

list(REMOVE_ITEM CMAKE_C_IMPLICIT_LINK_LIBRARIES ob)
target_link_libraries(${CMAKE_PROJECT_NAME} m)
