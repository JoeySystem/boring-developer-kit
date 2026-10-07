#pragma once
#include "board_mist_ui.h"

bool mist_grid_valid(int x, int y);
void mist_cell(board_mist_frame_t *f, int x, int y, float brightness);
void mist_cell_color(board_mist_frame_t *f, int x, int y, uint16_t color);
void mist_line(board_mist_frame_t *f, float x, float y, float end_x, float end_y, float brightness);
void mist_box(board_mist_frame_t *f, int x, int y, int w, int h, float brightness);
void mist_icon(board_mist_frame_t *f, board_mist_icon_t icon, float cx, float cy, float brightness);
void mist_text(board_mist_frame_t *f, const char *text, int row, float brightness);
void mist_aux(board_mist_frame_t *f, const char *text, int y, float brightness, int cx, unsigned read_index);
void mist_choices(board_mist_frame_t *f, bool confirm_selected, const char *left, const char *right, int y);
void mist_render_timer(board_mist_frame_t *f, const board_mist_view_t *view);
