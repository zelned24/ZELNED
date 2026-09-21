/**
 * Zel.NeD — UI (3DS dual-screen display with citro2d)
 */
#pragma once
#include "net_receiver.hpp"

// Initialize citro2d and fonts
bool ui_init();
void ui_exit();

// Draw one frame (call every tick from main loop)
void ui_draw(const ReceiverStats& stats);
