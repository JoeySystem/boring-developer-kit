#include "mist_glyph_motion.h"

#include <math.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static const mist_glyph_motion_profile_t profiles[MIST_PAGE_COUNT] = {
    {MIST_MOTION_SCATTER, 680}, {MIST_MOTION_NATIVE, 4340},
    {MIST_MOTION_CONTINUITY, 220}, {MIST_MOTION_GATHER, 640},
    {MIST_MOTION_SCATTER, 640}, {MIST_MOTION_GATHER, 600},
    {MIST_MOTION_ADJUST, 260}, {MIST_MOTION_ADJUST, 300},
    {MIST_MOTION_GATHER, 600}, {MIST_MOTION_TRACE, 480},
    {MIST_MOTION_TRACE, 360}, {MIST_MOTION_GATHER, 600},
    {MIST_MOTION_CONTINUITY, 220}, {MIST_MOTION_CONTINUITY, 180},
    {MIST_MOTION_CONTINUITY, 200}, {MIST_MOTION_SECTOR, 300},
    {MIST_MOTION_TRACE, 460}, {MIST_MOTION_ADJUST, 220},
    {MIST_MOTION_IMPACT, 420}, {MIST_MOTION_SCATTER, 460},
    {MIST_MOTION_IMPACT, 400}, {MIST_MOTION_TRACE, 300},
    {MIST_MOTION_GATHER, 300}, {MIST_MOTION_GATHER, 300},
    {MIST_MOTION_NATIVE, 2400}, {MIST_MOTION_NATIVE, 700},
    {MIST_MOTION_NONE, 0}, {MIST_MOTION_NONE, 0},
    {MIST_MOTION_TRACE, 360}, {MIST_MOTION_SCATTER, 620},
    {MIST_MOTION_SCATTER, 640}, {MIST_MOTION_GATHER, 440},
    {MIST_MOTION_TRACE, 460}, {MIST_MOTION_ADJUST, 220},
    {MIST_MOTION_ADJUST, 220},
};

mist_glyph_motion_profile_t mist_glyph_motion_profile(mist_glyph_page_id_t page)
{
    const mist_glyph_motion_profile_t none = {MIST_MOTION_NONE, 0};
    return page >= 0 && page < MIST_PAGE_COUNT ? profiles[page] : none;
}

void mist_glyph_motion_start(mist_glyph_motion_t *motion,
                             const mist_glyph_scene_t *from,
                             const mist_glyph_scene_t *target,
                             mist_glyph_motion_family_t family,
                             uint16_t duration_ms, uint32_t now_ms)
{
    if (motion == NULL || from == NULL || target == NULL) return;
    motion->from = *from;
    motion->target = *target;
    motion->family = family;
    motion->started_at_ms = now_ms;
    motion->duration_ms = duration_ms;
    motion->active = family != MIST_MOTION_NONE && duration_ms > 0;
    motion->trace_count = 0;
    memset(motion->trace_order, 0, sizeof(motion->trace_order));
    if (family == MIST_MOTION_TRACE) {
        static int8_t xs[MIST_GLYPH_VALID_CELL_COUNT];
        static int8_t ys[MIST_GLYPH_VALID_CELL_COUNT];
        static bool used[MIST_GLYPH_VALID_CELL_COUNT];
        unsigned count = 0;
        for (int y = MIST_GLYPH_GRID_MIN; y <= MIST_GLYPH_GRID_MAX; ++y)
            for (int x = MIST_GLYPH_GRID_MIN; x <= MIST_GLYPH_GRID_MAX; ++x) {
                const uint16_t color = mist_glyph_scene_get(target, x, y);
                if (color == 0 || color == MIST_GLYPH_MASKED_BLACK) continue;
                const uint16_t native = (uint16_t)((color << 8) | (color >> 8));
                const unsigned bright = (((native >> 11) & 31u) * 255u + 15u) / 31u;
                const unsigned green = (((native >> 5) & 63u) * 255u + 31u) / 63u;
                const unsigned blue = ((native & 31u) * 255u + 15u) / 31u;
                if (bright <= 24u && green <= 24u && blue <= 24u) continue;
                xs[count] = (int8_t)x;
                ys[count] = (int8_t)y;
                used[count++] = false;
            }
        int cursor_x = 12;
        int cursor_y = 0;
        for (unsigned order = 0; order < count; ++order) {
            unsigned best = 0;
            unsigned best_distance = UINT32_MAX;
            for (unsigned candidate = 0; candidate < count; ++candidate) {
                if (used[candidate]) continue;
                const int dx = xs[candidate] - cursor_x;
                const int dy = ys[candidate] - cursor_y;
                const unsigned distance = (unsigned)(dx * dx + dy * dy);
                if (distance < best_distance) {
                    best = candidate;
                    best_distance = distance;
                }
            }
            used[best] = true;
            cursor_x = xs[best];
            cursor_y = ys[best];
            const unsigned index = (unsigned)(cursor_y - MIST_GLYPH_GRID_MIN) *
                                   MIST_GLYPH_GRID_COUNT +
                                   (unsigned)(cursor_x - MIST_GLYPH_GRID_MIN);
            motion->trace_order[index] = count <= 1 ? 0u
                : (uint16_t)(order * 65535u / (count - 1u));
        }
        motion->trace_count = (uint16_t)count;
    }
}

static double clamp_unit(double value)
{
    return value <= 0.0 ? 0.0 : value >= 1.0 ? 1.0 : value;
}

static double smooth_unit(double value)
{
    value = clamp_unit(value);
    return value * value * (3.0 - 2.0 * value);
}

static int js_round(double value)
{
    return (int)floor(value + .5);
}

static uint16_t scale_color(uint16_t color, double gain)
{
    if (gain >= .999) return color;
    gain = clamp_unit(gain);
    const uint16_t native = (uint16_t)((color << 8) | (color >> 8));
    const unsigned red8 = (unsigned)floor(((native >> 11) & 31u) * 255.0 / 31.0 + .5);
    const unsigned green8 = (unsigned)floor(((native >> 5) & 63u) * 255.0 / 63.0 + .5);
    const unsigned blue8 = (unsigned)floor((native & 31u) * 255.0 / 31.0 + .5);
    const unsigned red = (unsigned)floor(red8 * gain + .5);
    const unsigned green = (unsigned)floor(green8 * gain + .5);
    const unsigned blue = (unsigned)floor(blue8 * gain + .5);
    const uint16_t scaled = (uint16_t)(((unsigned)floor(red * 31.0 / 255.0 + .5) << 11) |
        ((unsigned)floor(green * 63.0 / 255.0 + .5) << 5) |
        (unsigned)floor(blue * 31.0 / 255.0 + .5));
    return (uint16_t)((scaled << 8) | (scaled >> 8));
}

static uint32_t elapsed_ms(uint32_t now, uint32_t then)
{
    return now - then;
}

static unsigned hash_cell(int x, int y)
{
    return (unsigned)((x + 7) * 73 + (y + 9) * 151) % 101u;
}

static bool active_color(uint16_t color)
{
    if (color == 0 || color == MIST_GLYPH_MASKED_BLACK) return false;
    const uint16_t native = (uint16_t)((color << 8) | (color >> 8));
    const unsigned red = (((native >> 11) & 31u) * 255u + 15u) / 31u;
    const unsigned green = (((native >> 5) & 63u) * 255u + 31u) / 63u;
    const unsigned blue = ((native & 31u) * 255u + 15u) / 31u;
    return red > 24u || green > 24u || blue > 24u;
}

static bool blocked_by_overlay(const mist_glyph_scene_t *target, int x, int y)
{
    const int px = mist_glyph_cell_pixel(x);
    const int py = mist_glyph_cell_pixel(y);
    for (uint8_t index = 0; index < target->overlay_count; ++index) {
        const mist_glyph_overlay_t *overlay = &target->overlays[index];
        const int width = mist_glyph_aux_text_width(overlay->text);
        if (px <= overlay->x + width + 3 && px + 2 >= overlay->x - 3 &&
            py <= overlay->y + 14 + 3 && py + 2 >= overlay->y - 3)
            return true;
    }
    return false;
}

static void safe_position(double source_x, double source_y, int *x, int *y)
{
    *x = js_round(source_x);
    *y = js_round(source_y);
    for (int step = 0; step < 32 && !mist_glyph_cell_is_valid(*x, *y); ++step) {
        *x += *x < 12 ? 1 : *x > 12 ? -1 : 0;
        *y += *y < 11 ? 1 : *y > 11 ? -1 : 0;
    }
}

static void put_cell(mist_glyph_scene_t *output, const mist_glyph_scene_t *target,
                     int x, int y, uint16_t color)
{
    if (mist_glyph_cell_is_valid(x, y) && !blocked_by_overlay(target, x, y))
        mist_glyph_scene_set(output, x, y, color);
}

bool mist_glyph_motion_compose(const mist_glyph_motion_t *motion,
                               uint32_t now_ms, mist_glyph_scene_t *output)
{
    if (motion == NULL || output == NULL) return false;
    if (!motion->active || motion->duration_ms == 0 ||
        elapsed_ms(now_ms, motion->started_at_ms) >= motion->duration_ms) {
        *output = motion->target;
        return true;
    }
    const double progress = (double)elapsed_ms(now_ms, motion->started_at_ms) /
                            motion->duration_ms;
    if (motion->family == MIST_MOTION_CONTINUITY ||
        motion->family == MIST_MOTION_NATIVE) {
        *output = motion->target;
        return true;
    }
    const bool stable = motion->family == MIST_MOTION_ADJUST ||
                        motion->family == MIST_MOTION_SECTOR;
    if (!stable && progress <= 0.0) {
        *output = motion->from;
        return true;
    }
    *output = motion->target;
    if (!stable) {
        for (int y = MIST_GLYPH_GRID_MIN; y <= MIST_GLYPH_GRID_MAX; ++y)
            for (int x = MIST_GLYPH_GRID_MIN; x <= MIST_GLYPH_GRID_MAX; ++x)
                if (active_color(mist_glyph_scene_get(&motion->target, x, y)))
                    if (!blocked_by_overlay(&motion->target, x, y))
                        mist_glyph_scene_set(output, x, y, 0);
        if (progress < .38) {
            const double q = clamp_unit(progress / .38);
            for (int y = MIST_GLYPH_GRID_MIN; y <= MIST_GLYPH_GRID_MAX; ++y)
                for (int x = MIST_GLYPH_GRID_MIN; x <= MIST_GLYPH_GRID_MAX; ++x) {
                    const uint16_t color = mist_glyph_scene_get(&motion->from, x, y);
                    if (!active_color(color) || blocked_by_overlay(&motion->target, x, y))
                        continue;
                    double px = x;
                    double py = y;
                    double gain = 1.0 - q;
                    const double seed = hash_cell(x, y) / 101.0;
                    if (motion->family == MIST_MOTION_GATHER) {
                        const double travel = smooth_unit(q);
                        px = 12.0 + (x - 12.0) * (1.0 - travel);
                        py = 11.0 + (y - 11.0) * (1.0 - travel);
                        gain = q < .75 ? .8 : .8 * (1.0 - q) / .25;
                    } else if (motion->family == MIST_MOTION_SCATTER) {
                        px += sin(seed * 31.0) * 5.0 * q;
                        py -= 3.0 * q + seed * 3.0 * q;
                    }
                    int nx, ny;
                    safe_position(px, py, &nx, &ny);
                    put_cell(output, &motion->target, nx, ny, scale_color(color, gain));
                }
        }
        for (int pass = 0; pass < 2; ++pass)
            for (int y = MIST_GLYPH_GRID_MIN; y <= MIST_GLYPH_GRID_MAX; ++y)
                for (int x = MIST_GLYPH_GRID_MIN; x <= MIST_GLYPH_GRID_MAX; ++x) {
                    const uint16_t color = mist_glyph_scene_get(&motion->target, x, y);
                    if (!active_color(color)) continue;
                    const double seed = hash_cell(x, y) / 101.0;
                    const double group = (((x + 3) / 3 * 7 + (y + 3) / 3 * 11) % 7) / 7.0;
                    double q = 0.0;
                    double px = x;
                    double py = y;
                    if (motion->family == MIST_MOTION_SCATTER) {
                        const double delay = .08 + seed * .36;
                        q = clamp_unit((progress - delay) / .46);
                        if (progress < delay) continue;
                        const double angle = seed * M_PI * 2.0 + group;
                        const double radius = 6.0 + group * 7.0;
                        int sx, sy;
                        safe_position(x + cos(angle) * radius,
                                      y + sin(angle) * radius - 3.0, &sx, &sy);
                        const double travel = smooth_unit(q);
                        px = sx + (x - sx) * travel;
                        py = sy + (y - sy) * travel;
                    } else if (motion->family == MIST_MOTION_GATHER) {
                        q = clamp_unit((progress - .28) / .58);
                        if (q <= 0.0) continue;
                        const double travel = smooth_unit(q);
                        px = 12.0 + (x - 12.0) * travel;
                        py = 11.0 + (y - 11.0) * travel;
                    } else if (motion->family == MIST_MOTION_TRACE) {
                        const unsigned index = (unsigned)(y - MIST_GLYPH_GRID_MIN) *
                                               MIST_GLYPH_GRID_COUNT +
                                               (unsigned)(x - MIST_GLYPH_GRID_MIN);
                        const double order = motion->trace_order[index] / 65535.0;
                        q = clamp_unit((progress - .10 - order * .65) / .16);
                        if (q <= 0.0) continue;
                    } else {
                        q = 1.0 - pow(1.0 - clamp_unit((progress - .10 - group * .08) / .4), 3.0);
                        if (q <= 0.0) continue;
                    }
                    if ((pass == 0 && q >= 1.0) || (pass == 1 && q < 1.0)) continue;
                    if (q >= 1.0) put_cell(output, &motion->target, x, y, color);
                    else put_cell(output, &motion->target, js_round(px), js_round(py),
                                  scale_color(color, q > .8 ? 1.0 : q > .3 ? .7 : .42));
                }
    } else {
        for (int y = MIST_GLYPH_GRID_MIN; y <= MIST_GLYPH_GRID_MAX; ++y)
            for (int x = MIST_GLYPH_GRID_MIN; x <= MIST_GLYPH_GRID_MAX; ++x) {
                const uint16_t color = mist_glyph_scene_get(&motion->target, x, y);
                if (!active_color(color) || color == mist_glyph_scene_get(&motion->from, x, y) ||
                    blocked_by_overlay(&motion->target, x, y)) continue;
                const double seed = hash_cell(x, y) / 101.0;
                const double local = clamp_unit((progress - seed * .22) / .65);
                put_cell(output, &motion->target, x, y,
                         scale_color(color, 1.0 - .20 * sin(M_PI * local)));
            }
    }
    return true;
}

bool mist_glyph_motion_finished(const mist_glyph_motion_t *motion,
                                uint32_t now_ms)
{
    return motion == NULL || !motion->active || motion->duration_ms == 0 ||
           elapsed_ms(now_ms, motion->started_at_ms) >= motion->duration_ms;
}
