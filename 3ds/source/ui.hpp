/**
 * Zel.NeD — UI (3DS dual-screen display with citro2d)
 */
#pragma once
#include "net_receiver.hpp"

// Initialize citro2d and fonts
bool ui_init();
void ui_exit();

// Pass console name and IP for display on the top screen
void ui_set_console_info(const char* name, const char* ip);

// Draw one frame (call every tick from main loop)
void ui_draw(const ReceiverStats& stats);

// Clear UI log buffer
void ui_log_clear();
void ui_log(const char* msg);
