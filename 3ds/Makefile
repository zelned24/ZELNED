#---------------------------------------------------------------------------------
# Zel.NeD — Makefile for Nintendo 3DS (devkitARM + libctru + citro2d)
#---------------------------------------------------------------------------------
.SUFFIXES:

ifeq ($(strip $(DEVKITPRO)),)
  $(error "Please set DEVKITPRO in your environment. export DEVKITPRO=<path to devkitPro>")
endif

TOPDIR ?= $(CURDIR)
include $(DEVKITPRO)/rules/3ds.mk

#---------------------------------------------------------------------------------
# Target name
#---------------------------------------------------------------------------------
TARGET    := ZelNeD
BUILD     := build
SOURCES   := source source/lz4
INCLUDES  := source source/lz4
DATA      := data
ROMFS     := romfs

#---------------------------------------------------------------------------------
# Options for code generation
#---------------------------------------------------------------------------------
ARCH     := -march=armv6k -mtune=mpcore -mfloat-abi=hard -mtp=soft

CFLAGS   := -g -Wall -O2 -mword-relocations \
             -fomit-frame-pointer -ffunction-sections \
             $(ARCH) \
             $(INCLUDE) \
             -DARM11 -D__3DS__

CXXFLAGS := $(CFLAGS) -fno-rtti -fno-exceptions -std=gnu++17

ASFLAGS  := -g $(ARCH)

LDFLAGS  := -specs=3dsx.specs -g $(ARCH) -Wl,-Map,$(notdir $*.map)

LIBS     := -lcitro2d -lcitro3d -lctru -lm

#---------------------------------------------------------------------------------
# App metadata (for 3DSX banner)
#---------------------------------------------------------------------------------
APP_TITLE    := Zel.NeD
APP_AUTHOR   := DZ Project
APP_ICON     := $(TOPDIR)/icon.png

include $(DEVKITPRO)/rules/3ds.mk
