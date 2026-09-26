#include "mist_glyph_pages.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

const mist_glyph_page_descriptor_t mist_glyph_page_catalog[MIST_PAGE_COUNT] = {
    {"home", 1}, {"boot", 1}, {"battery", 1}, {"function", 2},
    {"settings", 2}, {"system", 2}, {"haptic", 3}, {"lighting", 3},
    {"standby", 3}, {"restart-confirm", 3}, {"quick", 3},
    {"timer-setup", 4}, {"timer-run", 4}, {"timer-pause", 4},
    {"timer-cancel", 4}, {"prompt", 2}, {"ble", 5},
    {"ble-progress", 5}, {"success", 5}, {"error", 5}, {"warning", 4},
    {"play", 6}, {"pause", 6}, {"cancel", 5}, {"restart", 3},
    {"shutdown", 1}, {"sleep", 1}, {"bmr", 6}, {"ble-confirm", 5},
    {"icon-choice", 6}, {"all-carousel", 2}, {"system-text", 2},
    {"status-text", 1}, {"two-choice-text", 6}, {"local-text", 6},
};

static const uint8_t page_icon_resources[] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
    22, 23, 26, 20, 21,
};

static uint16_t gray_level(unsigned level)
{
    return mist_glyph_gray_level((uint8_t)(level > 255u ? 255u : level));
}

static unsigned level_from_unit(double value)
{
    if (value <= 0.0) return 0u;
    if (value >= 1.0) return 255u;
    return (unsigned)floor(value * 255.0 + 0.5);
}

static void scene_line(mist_glyph_scene_t *scene, int x0, int y0,
                       int x1, int y1, uint16_t color)
{
    int dx = x1 - x0;
    int dy = y1 - y0;
    const int steps = (dx < 0 ? -dx : dx) > (dy < 0 ? -dy : dy)
        ? (dx < 0 ? -dx : dx) : (dy < 0 ? -dy : dy);
    for (int step = 0; step <= steps; ++step) {
        const int x = x0 + (steps == 0 ? 0
            : (int)floor((double)dx * step / steps + 0.5));
        const int y = y0 + (steps == 0 ? 0
            : (int)floor((double)dy * step / steps + 0.5));
        mist_glyph_scene_set(scene, x, y, color);
    }
}

static void scene_box(mist_glyph_scene_t *scene, int x, int y, int width,
                      int height, uint16_t color)
{
    for (int row = 0; row < height; ++row)
        for (int column = 0; column < width; ++column)
            mist_glyph_scene_set(scene, x + column, y + row, color);
}

void mist_glyph_page_context_default(mist_glyph_page_context_t *context)
{
    if (context == NULL) return;
    memset(context, 0, sizeof(*context));
    context->battery_percent = 82;
    context->item_count = 2;
    context->level = 5;
    context->max_level = 9;
    context->slot = 1;
    context->progress_percent = 62;
    context->minutes = 15;
    context->total_minutes = 25;
}

static void draw_icon_centered(mist_glyph_scene_t *scene, uint8_t resource,
                               int center_y, uint16_t color)
{
    const mist_glyph_icon_t *icon = mist_glyph_icon_resolve(resource);
    if (icon == NULL) return;
    mist_glyph_scene_draw_icon(scene, resource,
                               12 - icon->width / 2,
                               center_y - icon->height / 2, color);
}

static void draw_icon_scaled(mist_glyph_scene_t *scene, uint8_t resource,
                             int center_x, int center_y, int max_size,
                             uint16_t color)
{
    const mist_glyph_icon_t *icon = mist_glyph_icon_resolve(resource);
    if (icon == NULL) return;
    const int largest = icon->width > icon->height ? icon->width : icon->height;
    const int width = largest > max_size
        ? (icon->width * max_size + largest / 2) / largest : icon->width;
    const int height = largest > max_size
        ? (icon->height * max_size + largest / 2) / largest : icon->height;
    const int x0 = center_x - width / 2;
    const int y0 = center_y - height / 2;
    for (int y = 0; y < height; ++y) for (int x = 0; x < width; ++x) {
        const int source_x = largest > max_size ? x * largest / max_size
                                                 : x;
        const int source_y = largest > max_size ? y * largest / max_size
                                                 : y;
        if (icon->rows[source_y] & (1u << (icon->width - 1u - source_x)))
            mist_glyph_scene_set(scene, x0 + x, y0 + y, color);
    }
}

static void draw_icon_centered_percent_levels(mist_glyph_scene_t *scene,
                                              uint8_t resource, int center_y,
                                              unsigned base_thousand)
{
    const mist_glyph_icon_t *icon = mist_glyph_icon_resolve(resource);
    if (icon == NULL) return;
    const int x0 = 12 - icon->width / 2;
    const int y0 = center_y - icon->height / 2;
    for (uint8_t y = 0; y < icon->height; ++y)
        for (uint8_t x = 0; x < icon->width; ++x) {
            if ((icon->rows[y] & (1u << (icon->width - 1u - x))) == 0) continue;
            unsigned percent = 100u;
            if (icon->levels != NULL) {
                const unsigned stored = icon->levels[y * icon->width + x];
                percent = stored == 204u ? 80u : stored == 235u ? 92u
                    : (stored * 100u + 127u) / 255u;
            }
            const unsigned gain = (base_thousand * percent * 255u + 50000u) /
                                  100000u;
            mist_glyph_scene_set(scene, x0 + x, y0 + y, gray_level(gain));
        }
}

static void draw_text_centered(mist_glyph_scene_t *scene, const char *text,
                               int y, uint16_t color)
{
    mist_glyph_scene_draw_text(scene, text,
                               (25 - mist_glyph_text_columns(text)) / 2,
                               y, color);
}

static void add_overlay_at(mist_glyph_scene_t *scene, const char *text,
                           int y, uint16_t color, int center_x)
{
    if (text == NULL) return;
    const int top_delta2 = 2 * y - 127;
    const int top_distance2 = top_delta2 < 0 ? -top_delta2 : top_delta2;
    const int bottom = y + 13;
    const int bottom_delta2 = 2 * bottom - 127;
    const int bottom_distance2 = bottom_delta2 < 0 ? -bottom_delta2 : bottom_delta2;
    const int distance2 = top_distance2 > bottom_distance2
        ? top_distance2 : bottom_distance2;
    int half_width = 0;
    while (half_width < 62 &&
           4 * (half_width + 1) * (half_width + 1) +
           distance2 * distance2 <= 125 * 125) ++half_width;
    const int left = 64 - half_width + 3;
    const int right = 64 + half_width - 3;
    const int available = center_x - left < right - center_x
        ? center_x - left : right - center_x;
    const int column_limit = available > 0 ? available * 2 / 3 : 1;
    char fitted[MIST_GLYPH_OVERLAY_TEXT_LENGTH] = {0};
    size_t length = 0;
    for (const char *cursor = text; *cursor != '\0' &&
         length + 1 < sizeof(fitted); ++cursor) {
        fitted[length] = *cursor;
        fitted[length + 1] = '\0';
        if (mist_glyph_text_columns(fitted) > column_limit) {
            fitted[length] = '\0';
            break;
        }
        ++length;
    }
    if (length == 0) {
        fitted[0] = '?';
        fitted[1] = '\0';
    }
    const int text_width = mist_glyph_aux_text_width(fitted);
    const int text_x = center_x - text_width / 2;
    for (int gy = MIST_GLYPH_GRID_MIN; gy <= MIST_GLYPH_GRID_MAX; ++gy)
        for (int gx = MIST_GLYPH_GRID_MIN; gx <= MIST_GLYPH_GRID_MAX; ++gx) {
            const int px = mist_glyph_cell_pixel(gx);
            const int py = mist_glyph_cell_pixel(gy);
            if (px <= text_x + text_width && px + 2 >= text_x - 1 &&
                py <= y + 14 && py + 2 >= y - 1)
                mist_glyph_scene_mask_black(scene, gx, gy);
        }
    mist_glyph_scene_add_overlay(scene, fitted,
                                 text_x,
                                 y, color, MIST_GLYPH_BLACK);
}

static void add_overlay_centered(mist_glyph_scene_t *scene, const char *text,
                                 int y, uint16_t color)
{
    add_overlay_at(scene, text, y, color, 64);
}

static void draw_marks(mist_glyph_scene_t *scene, unsigned count,
                       unsigned selected)
{
    if (count == 0 || count > 8) return;
    for (unsigned index = 0; index < count; ++index)
        mist_glyph_scene_set(scene, 12 - (int)(count - 1) + (int)index * 2,
                             25, index == selected ? gray_level(242)
                                                   : gray_level(31));
}

static void draw_marks_row(mist_glyph_scene_t *scene, unsigned count,
                           unsigned selected, int row)
{
    if (count == 0 || count > 8) return;
    for (unsigned index = 0; index < count; ++index)
        mist_glyph_scene_set(scene, 12 - (int)(count - 1) + (int)index * 2,
                             row, index == selected ? gray_level(242)
                                                    : gray_level(31));
}

static int clamp_percent(int value)
{
    if (value < 0) return -1;
    if (value > 100) return 100;
    return value;
}

static void format_percent(char output[4], int percent)
{
    if (percent < 0) {
        strcpy(output, "--");
    } else if (percent >= 100) {
        strcpy(output, "100");
    } else if (percent >= 10) {
        output[0] = (char)('0' + percent / 10);
        output[1] = (char)('0' + percent % 10);
        output[2] = '\0';
    } else {
        output[0] = (char)('0' + percent);
        output[1] = '\0';
    }
}

static void build_home(const mist_glyph_page_context_t *context,
                       mist_glyph_scene_t *scene)
{
    static const uint8_t resources[] = {22, 23, 24};
    const unsigned mode = context->mode <= MIST_MODE_CLAUDE_CODE
        ? (unsigned)context->mode : 0u;
    draw_icon_centered(scene, resources[mode], 12, gray_level(242));
}

static void build_boot(mist_glyph_scene_t *scene);

static bool boring_cell_at(int x, int y)
{
    for (size_t index = 0; index < MIST_GLYPH_BORING_CELL_COUNT; ++index)
        if (mist_glyph_boring_cells[index].x == x &&
            mist_glyph_boring_cells[index].y == y) return true;
    return false;
}

static void draw_boot_loader(mist_glyph_scene_t *scene, uint32_t elapsed_ms)
{
    const int travel = (int)(elapsed_ms * 10000U / MIST_GLYPH_BOOT_LOADER_MS) - 2000;
    for (unsigned index = 0; index < 7; ++index) {
        int distance = (int)index * 1000 - travel;
        if (distance < 0) distance = -distance;
        int pulse = distance >= 1700 ? 0 : 1000 - distance * 1000 / 1700;
        const int size = pulse >= 650 ? 3 : pulse >= 250 ? 2 : 1;
        const int offset = size == 1 ? 0 : -1;
        const unsigned gain = 77u + (unsigned)(pulse * pulse * (3000 - 2 * pulse) /
                                               1000000) * 166u / 1000u;
        const int center_x = 12 + ((int)index - 3) * 4;
        for (int y = 0; y < size; ++y)
            for (int x = 0; x < size; ++x)
                mist_glyph_scene_set(scene, center_x + offset + x, 12 + offset + y,
                                     gray_level(gain));
    }
}

static unsigned smooth_thousand(int value)
{
    if (value <= 0) return 0;
    if (value >= 1000) return 1000;
    return (unsigned)(value * value * (3000 - 2 * value) / 1000000);
}

static int round_ratio(int numerator, int denominator)
{
    int adjusted = numerator + denominator / 2;
    return adjusted >= 0 ? adjusted / denominator
                         : -((-adjusted + denominator - 1) / denominator);
}

static void draw_spread_loader(mist_glyph_scene_t *scene, unsigned spread,
                               uint32_t loader_elapsed, bool active)
{
    if (active) {
        draw_boot_loader(scene, loader_elapsed);
        return;
    }
    for (int index = 0; index < 7; ++index) {
        const int x = 12 + round_ratio((index - 3) * 4 * (int)spread, 1000);
        mist_glyph_scene_set(scene, x, 12, gray_level(77));
    }
}

bool mist_glyph_boot_frame(uint32_t elapsed_ms, mist_glyph_mode_t mode,
                           mist_glyph_scene_t *scene)
{
    if (scene == NULL) return false;
    mist_glyph_scene_clear(scene, MIST_GLYPH_GRID_DIM);
    if (elapsed_ms >= MIST_GLYPH_BOOT_TOTAL_MS) {
        mist_glyph_page_context_t context;
        mist_glyph_page_context_default(&context);
        context.mode = mode;
        build_home(&context, scene);
        return true;
    }
    if (elapsed_ms < MIST_GLYPH_BOOT_LOGO_MS) {
        const unsigned visible = elapsed_ms / MIST_GLYPH_BOOT_FRAME_MS;
        unsigned order = 0;
        for (int y = 10; y <= 14; ++y) {
            const bool reverse = ((y - 10) & 1) != 0;
            for (int step = 0; step < 31; ++step) {
                const int x = reverse ? MIST_GLYPH_GRID_MAX - step
                                      : MIST_GLYPH_GRID_MIN + step;
                if (!boring_cell_at(x, y)) continue;
                if (order++ < visible)
                    mist_glyph_scene_set(scene, x, y, gray_level(235));
            }
        }
        return true;
    }
    elapsed_ms -= MIST_GLYPH_BOOT_LOGO_MS;
    if (elapsed_ms < MIST_GLYPH_BOOT_HOLD_MS) {
        build_boot(scene);
        return true;
    }
    elapsed_ms -= MIST_GLYPH_BOOT_HOLD_MS;
    if (elapsed_ms < MIST_GLYPH_BOOT_COLLAPSE_MS) {
        const unsigned vertical = smooth_thousand((int)elapsed_ms * 1000 / 120);
        const unsigned horizontal = smooth_thousand(((int)elapsed_ms - 80) * 1000 / 220);
        for (size_t index = 0; index < MIST_GLYPH_BORING_CELL_COUNT; ++index) {
            const int x = 12 + round_ratio((mist_glyph_boring_cells[index].x - 12) *
                                           (int)(1000 - horizontal), 1000);
            const int y = 12 + round_ratio((mist_glyph_boring_cells[index].y - 12) *
                                           (int)(1000 - vertical), 1000);
            mist_glyph_scene_set(scene, x, y, gray_level(235));
        }
        return true;
    }
    elapsed_ms -= MIST_GLYPH_BOOT_COLLAPSE_MS;
    if (elapsed_ms < MIST_GLYPH_BOOT_SPREAD_MS) {
        draw_spread_loader(scene,
            smooth_thousand((int)elapsed_ms * 1000 / MIST_GLYPH_BOOT_SPREAD_MS),
            0, false);
        return true;
    }
    elapsed_ms -= MIST_GLYPH_BOOT_SPREAD_MS;
    if (elapsed_ms < MIST_GLYPH_BOOT_LOADER_MS) {
        draw_spread_loader(scene, 1000, elapsed_ms, true);
        return true;
    }
    elapsed_ms -= MIST_GLYPH_BOOT_LOADER_MS;
    if (elapsed_ms < MIST_GLYPH_BOOT_CLOSE_MS) {
        draw_spread_loader(scene,
            1000 - smooth_thousand((int)elapsed_ms * 1000 / MIST_GLYPH_BOOT_CLOSE_MS),
            0, false);
        return true;
    }
    elapsed_ms -= MIST_GLYPH_BOOT_CLOSE_MS;
    mist_glyph_page_context_t context;
    mist_glyph_page_context_default(&context);
    context.mode = mode;
    static const uint8_t resources[] = {22, 23, 24};
    const unsigned gain = 242u * smooth_thousand((int)elapsed_ms * 1000 /
                                                  MIST_GLYPH_BOOT_EXPAND_MS) / 1000u;
    draw_icon_centered(scene, resources[mode <= MIST_MODE_CLAUDE_CODE ? mode : 0],
                       12, gray_level(gain));
    return true;
}

static void build_boot(mist_glyph_scene_t *scene)
{
    for (size_t index = 0; index < MIST_GLYPH_BORING_CELL_COUNT; ++index)
        mist_glyph_scene_set(scene, mist_glyph_boring_cells[index].x,
                            mist_glyph_boring_cells[index].y, gray_level(235));
}

typedef struct {
    int8_t x;
    int8_t y;
    double score;
} energy_cell_t;

static unsigned collect_energy_cells(energy_cell_t *cells)
{
    unsigned count = 0;
    for (int y = MIST_GLYPH_GRID_MIN; y <= MIST_GLYPH_GRID_MAX; ++y)
        for (int x = MIST_GLYPH_GRID_MIN; x <= MIST_GLYPH_GRID_MAX; ++x)
            if (mist_glyph_cell_is_valid(x, y))
                cells[count++] = (energy_cell_t){(int8_t)x, (int8_t)y,
                                                  y + .32 * cos(x * .55)};
    for (unsigned i = 1; i < count; ++i) {
        const energy_cell_t value = cells[i];
        unsigned j = i;
        while (j > 0 && cells[j - 1].score < value.score) {
            cells[j] = cells[j - 1];
            --j;
        }
        cells[j] = value;
    }
    return count;
}

static void build_battery(const mist_glyph_page_context_t *context,
                          mist_glyph_scene_t *scene)
{
    const int percent = clamp_percent(context->battery_percent);
    if (percent >= 0) {
        static energy_cell_t cells[MIST_GLYPH_VALID_CELL_COUNT];
        static unsigned count;
        if (count == 0) count = collect_energy_cells(cells);
        const unsigned target = (count * (unsigned)percent + 50u) / 100u;
        const bool active = context->charging && context->animation_phase > 0u &&
                            context->animation_phase < 255u;
        const double phase = context->animation_phase / 255.0;
        const double envelope = active ? pow(sin(M_PI * phase), 2.0) : 0.0;
        for (unsigned index = 0; index < target; ++index) {
            const int x = cells[index].x;
            const int y = cells[index].y;
            const double base = .26 + .12 * (y + 3) / 30.0 +
                                .035 * cos(x * .3 + y * .24);
            const double travel = 27.0 - phase * 37.0;
            const double curve = .8 * sin(x * .25);
            const double crest = exp(-pow((y - travel + curve) / 3.5, 2.0));
            const double trail = exp(-pow((y - travel - 4.0 + curve) / 5.5, 2.0));
            double distance = hypot((x - 12) / 7.0, (y - 12) / 4.0);
            if (distance > 1.0) distance = 1.0;
            const double readability = .7 + .3 * distance;
            const double value = (base + envelope * (.22 * crest + .07 * trail)) *
                                 readability;
            mist_glyph_scene_set(scene, x, y,
                                 gray_level(level_from_unit(value)));
        }
    }
    char value[4];
    format_percent(value, percent);
    const uint16_t color = percent < 0 ? gray_level(170)
        : percent >= 80 ? MIST_GLYPH_GREEN
        : percent >= 40 ? MIST_GLYPH_ORANGE : MIST_GLYPH_RED;
    draw_text_centered(scene, value, 10, color);
}

static unsigned function_item_count(const mist_glyph_page_context_t *context)
{
    return context->item_count >= 2 && context->item_count <= 4
        ? context->item_count : 2u;
}

static uint8_t function_icon(unsigned selected)
{
    static const uint8_t resources[] = {0, 1, 27, 23};
    return resources[selected];
}

static void build_carousel(mist_glyph_page_id_t page,
                           const mist_glyph_page_context_t *context,
                           mist_glyph_scene_t *scene)
{
    if (page == MIST_PAGE_SYSTEM_TEXT) {
        draw_text_centered(scene, context->selected % 2 == 0 ? "MAC" : "WIN",
                           10, gray_level(242));
        draw_marks(scene, 2, context->selected % 2);
        return;
    }
    uint8_t resource = 0;
    if (page == MIST_PAGE_FUNCTION)
        resource = function_icon(context->selected % function_item_count(context));
    else if (page == MIST_PAGE_SYSTEM) resource = context->selected % 2 == 0 ? 2 : 3;
    else if (page == MIST_PAGE_SETTINGS) {
        static const uint8_t settings[] = {21, 14, 13, 15, 20};
        resource = settings[context->selected % 5];
    } else resource = page_icon_resources[context->selected % 22];
    draw_icon_centered(scene, resource, 11, gray_level(242));
    if (page == MIST_PAGE_FUNCTION)
        add_overlay_centered(scene, resource == 0 ? "FOCUS"
                             : resource == 1 ? "SETTINGS"
                             : resource == 27 ? "BATTERY" : "CODEX", 94,
                             gray_level(204));
    if (page == MIST_PAGE_ALL_CAROUSEL) {
        static const char *const labels[] = {
            "FOCUS", "SETTINGS", "MACOS", "WIN/LINUX", "BACK", "OK",
            "POWER", "CHECK", "PLAY", "PAUSED", "STOP", "ERROR", "PROFILE",
            "LIGHT", "HAPTIC", "SLEEP", "EXIT", "NOR", "CODEX", "BLE",
            "RESTART", "SYSTEM",
        };
        if (resource != 2 && resource != 3)
            add_overlay_centered(scene, labels[context->selected % 22], 94,
                                 gray_level(204));
        char position[8];
        snprintf(position, sizeof(position), "%u/22",
                 (unsigned)(context->selected % 22) + 1u);
        add_overlay_centered(scene, position, 111, gray_level(115));
    } else {
        const unsigned count = page == MIST_PAGE_SETTINGS ? 5u
            : page == MIST_PAGE_FUNCTION ? function_item_count(context) : 2u;
        draw_marks(scene, count, context->selected % count);
    }
}

void mist_glyph_function_carousel_frame(const mist_glyph_page_context_t *context,
                                         float position, mist_glyph_scene_t *scene)
{
    mist_glyph_scene_clear(scene, MIST_GLYPH_GRID_DIM);
    /* The resting frame retains the current icon size, label and page marks. */
    const int count = (int)function_item_count(context);
    const int nearest = (int)roundf(position);
    if (fabsf(position - nearest) < .0001f) {
        mist_glyph_page_context_t resting = *context;
        resting.selected = (uint8_t)((nearest % count + count) % count);
        build_carousel(MIST_PAGE_FUNCTION, &resting, scene);
        return;
    }
    /* Original MIST wheel: neighbouring icons follow a shallow circular arc. */
    for (int k = (int)floorf(position); k <= (int)ceilf(position); ++k) {
        const float offset = k - position;
        if (fabsf(offset) > .98f) continue;
        const float angle = offset * (float)M_PI / 3.0f;
        const int x = (int)lroundf(12.0f + sinf(angle) * 13.0f);
        const int y = (int)lroundf(11.0f + (1.0f - cosf(angle)) * 9.0f);
        const uint8_t resource = function_icon((unsigned)((k % count + count) % count));
        const mist_glyph_icon_t *icon = mist_glyph_icon_resolve(resource);
        mist_glyph_scene_draw_icon(scene, resource, x - icon->width / 2,
                                   y - icon->height / 2,
                                   gray_level((unsigned)(242.0f *
                                       (1.0f - fabsf(offset) * .55f))));
    }
    draw_marks(scene, (unsigned)count, context->selected % count);
}

static void build_icon_choice(const mist_glyph_page_context_t *context,
                              mist_glyph_scene_t *scene)
{
    add_overlay_centered(scene, "SELECT", 22, gray_level(153));
    draw_icon_scaled(scene, 2, 6, 11, 9,
                     gray_level(context->confirm_selected ? 64u : 242u));
    draw_icon_scaled(scene, 3, 18, 11, 9,
                     gray_level(context->confirm_selected ? 242u : 64u));
    add_overlay_centered(scene, context->confirm_selected ? "WIN/LINUX" : "MACOS",
                         90, gray_level(204));
    draw_marks(scene, 2, context->confirm_selected ? 1 : 0);
}

static void build_haptic(const mist_glyph_page_context_t *context,
                         mist_glyph_scene_t *scene)
{
    const unsigned maximum = context->max_level > 0 && context->max_level < 9
        ? context->max_level : 9;
    const unsigned level = context->level > maximum ? maximum : context->level;
    const int center_x = 12;
    const int center_y = 10;
    static const int radii[] = {3, 6, 9};
    static const double gains[] = {.8, .42, .15};
    const double strength = level / (double)maximum;
    for (unsigned ring = 0; ring < 3; ++ring) {
        const int radius = radii[ring];
        for (int y = center_y - radius - 1; y <= center_y + radius + 1; ++y)
            for (int x = center_x - radius - 1; x <= center_x + radius + 1; ++x) {
                const double angle = fmod(atan2(y - center_y, x - center_x) *
                                          180.0 / M_PI + 360.0, 360.0);
                const bool in_arc = (angle >= 130.0 && angle <= 230.0) ||
                                    angle >= 310.0 || angle <= 50.0;
                if (in_arc && fabs(hypot(x - center_x, y - center_y) - radius) < .48)
                    mist_glyph_scene_set(scene, x, y,
                        gray_level(level_from_unit(gains[ring] *
                                   (.18 + strength * .82))));
            }
    }
    scene_box(scene, 11, 8, 3, 5,
              gray_level(level_from_unit(.35 + strength * .55)));
    char value[3];
    snprintf(value, sizeof(value), "%u", level);
    add_overlay_centered(scene,
        context->state == MIST_STATE_APPLYING ? "WAIT" :
        context->state == MIST_STATE_ERROR ? "ERROR" :
        !context->confirm_selected ? "BACK" : "HAPTIC", 23,
        gray_level(context->state == MIST_STATE_ERROR ? 242 :
                   context->state == MIST_STATE_APPLYING ? 128 : 153));
    draw_text_centered(scene, value, 16, gray_level(217));
    char range[6];
    snprintf(range, sizeof(range), "/%u", maximum);
    add_overlay_at(scene, range, 92, gray_level(128), 89);
}

static void build_lighting(const mist_glyph_page_context_t *context,
                           mist_glyph_scene_t *scene)
{
    const unsigned maximum = context->max_level > 0 && context->max_level < 9
        ? context->max_level : 9;
    const unsigned level = context->level > maximum ? maximum : context->level;
    for (int y = 5; y < 17; ++y) for (int x = 6; x < 19; ++x) {
        const double distance = hypot(x - 12.0, y - 10.5);
        if (distance >= 5.5) continue;
        double field = 1.0 - distance / 5.8;
        if (field < .09) field = .09;
        const double strength = level == 0 ? 0.0 : .6 + .4 * level / maximum;
        mist_glyph_scene_set(scene, x, y,
            gray_level(level_from_unit(pow(field, 1.3) * strength)));
    }
    add_overlay_centered(scene, "LIGHT", 22, gray_level(140));
    if (context->state == MIST_STATE_APPLYING)
        add_overlay_centered(scene, "WAIT", 100, gray_level(204));
    else if (context->state == MIST_STATE_ERROR)
        add_overlay_centered(scene, "ERROR", 100, gray_level(204));
    else if (!context->confirm_selected)
        add_overlay_centered(scene, "BACK", 100, gray_level(204));
    else {
        char value[8];
        snprintf(value, sizeof(value), "%u / %u", level, maximum);
        add_overlay_centered(scene, value, 100, gray_level(204));
    }
}

static void build_standby(const mist_glyph_page_context_t *context,
                          mist_glyph_scene_t *scene)
{
    draw_icon_centered(scene, 15, 9, gray_level(229));
    char value[4];
    if (context->minutes == 0) strcpy(value, "OFF");
    else snprintf(value, sizeof(value), "%u", context->minutes);
    draw_text_centered(scene, value, 15, gray_level(222));
    if (context->state == MIST_STATE_APPLYING)
        add_overlay_centered(scene, "WAIT", 105, gray_level(153));
    else if (context->state == MIST_STATE_ERROR)
        add_overlay_centered(scene, "ERROR", 105, gray_level(153));
    else if (!context->confirm_selected)
        add_overlay_centered(scene, "BACK", 105, gray_level(153));
    else
        add_overlay_centered(scene, context->minutes == 0 ? "SLEEP OFF" : "MIN",
                             105, gray_level(153));
}

static void draw_choices(mist_glyph_scene_t *scene, bool selected,
                         const char *left, const char *right, int y)
{
    add_overlay_at(scene, left, y, gray_level(selected ? 89 : 230), 40);
    add_overlay_at(scene, right, y, gray_level(selected ? 230 : 89), 88);
    scene_line(scene, selected ? 15 : 6, 26, selected ? 19 : 10, 26,
               gray_level(204));
}

static void build_restart_choice(const mist_glyph_page_context_t *context,
                                 mist_glyph_scene_t *scene)
{
    draw_icon_centered(scene, 20, 7, gray_level(242));
    add_overlay_centered(scene, "RESTART?", 66, gray_level(207));
    /* Keep the complete action labels inside the circular aperture. */
    draw_choices(scene, context->confirm_selected, "BACK", "YES", 86);
}

static void build_ble_choice(const mist_glyph_page_context_t *context,
                             mist_glyph_scene_t *scene)
{
    draw_icon_centered_percent_levels(scene, 19, 8, 700u);
    char host[10];
    snprintf(host, sizeof(host), "HOST %u", context->slot);
    add_overlay_centered(scene, host, 69, gray_level(204));
    draw_choices(scene, context->confirm_selected, "NO", "YES", 86);
}

static void format_time(char value[6], const mist_glyph_page_context_t *context)
{
    const uint8_t minutes = context->minutes > 99 ? 99 : context->minutes;
    const uint8_t seconds = context->seconds > 59 ? 59 : context->seconds;
    value[0] = (char)('0' + minutes / 10);
    value[1] = (char)('0' + minutes % 10);
    value[2] = ':';
    value[3] = (char)('0' + seconds / 10);
    value[4] = (char)('0' + seconds % 10);
    value[5] = '\0';
}

static void build_timer_setup(const mist_glyph_page_context_t *context,
                              mist_glyph_scene_t *scene)
{
    char minutes[3];
    snprintf(minutes, sizeof(minutes), "%02u", context->minutes > 99
                                               ? 99 : context->minutes);
    add_overlay_centered(scene, "TIMER", 25, gray_level(168));
    draw_text_centered(scene, minutes, 9, gray_level(242));
    add_overlay_centered(scene, "MIN", 75, gray_level(133));
    scene_line(scene, 7, 24, 9, 22,
               gray_level(context->confirm_selected ? 89 : 229));
    scene_line(scene, 7, 24, 9, 26,
               gray_level(context->confirm_selected ? 89 : 229));
    scene_box(scene, 15, 23, 1, 3,
              gray_level(context->confirm_selected ? 242 : 89));
    mist_glyph_scene_set(scene, 16, 24,
                         gray_level(context->confirm_selected ? 242 : 89));
}

typedef struct {
    int8_t x;
    int8_t y;
    int16_t score;
} sand_cell_t;

static unsigned collect_sand_cells(unsigned amount_thousand, sand_cell_t *cells)
{
    unsigned count = 0;
    unsigned relax = amount_thousand <= 400u ? 0u
        : amount_thousand >= 650u ? 1000u
        : (amount_thousand - 400u) * 4u;
    const unsigned smooth = relax * relax * (3000u - 2u * relax) / 1000000u;
    const int slope = (int)(125u * (1000u - smooth) / 1000u);
    for (int y = MIST_GLYPH_GRID_MIN; y <= MIST_GLYPH_GRID_MAX; ++y)
        for (int x = MIST_GLYPH_GRID_MIN; x <= MIST_GLYPH_GRID_MAX; ++x)
            if (mist_glyph_cell_is_valid(x, y)) {
                const int dx = x - 12;
                cells[count++] = (sand_cell_t){(int8_t)x, (int8_t)y,
                    (int16_t)(y * 100 - slope * (dx < 0 ? -dx : dx))};
            }
    for (unsigned i = 1; i < count; ++i) {
        const sand_cell_t value = cells[i];
        unsigned j = i;
        while (j > 0 && (cells[j - 1].score < value.score ||
               (cells[j - 1].score == value.score && cells[j - 1].x > value.x))) {
            cells[j] = cells[j - 1];
            --j;
        }
        cells[j] = value;
    }
    return count;
}

static void draw_sand(const mist_glyph_page_context_t *context,
                      unsigned dim_thousand, bool falling,
                       mist_glyph_scene_t *scene)
{
    const unsigned total = context->total_minutes * 60u;
    const unsigned remaining = context->minutes * 60u + context->seconds;
    const unsigned used = remaining >= total ? 0u : total - remaining;
    const unsigned amount_thousand = total == 0 ? 0u : used * 1000u / total;
    static sand_cell_t cells[MIST_GLYPH_VALID_CELL_COUNT];
    static unsigned cached_amount = 1001u;
    static unsigned count;
    if (cached_amount != amount_thousand) {
        count = collect_sand_cells(amount_thousand, cells);
        cached_amount = amount_thousand;
    }
    const unsigned filled = total == 0 ? 0u : (count * used + total / 2u) / total;
    int surface[31];
    for (unsigned i = 0; i < 31; ++i) surface[i] = 28;
    for (unsigned index = 0; index < filled; ++index) {
        const int x = cells[index].x;
        const int y = cells[index].y;
        const int variation = (x * 73 + y * 37) % 101;
        const unsigned tone = (unsigned)(dim_thousand * 255u *
                              (unsigned)(250 + variation) + 250000u) / 500000u;
        mist_glyph_scene_set(scene, x, y, gray_level(tone));
        if (y < surface[x - MIST_GLYPH_GRID_MIN])
            surface[x - MIST_GLYPH_GRID_MIN] = y;
    }
    if (falling && (context->animation_phase % 20u) > 0u &&
        amount_thousand < 1000u) {
        static const int8_t grain_x[] = {12, 11, 13};
        const double phase = (context->animation_phase % 20u) / 20.0;
        for (unsigned grain = 0; grain < 3; ++grain) {
            const int x = grain_x[grain];
            int impact = surface[x - MIST_GLYPH_GRID_MIN];
            if (impact == 28) impact = MIST_GLYPH_GRID_MAX;
            const int start = -2;
            if (impact <= start) continue;
            const double t = fmod(phase + grain / 3.0, 1.0);
            const double flight = fmin(1.0, t / .8);
            int y = (int)floor(start + (impact - start) * flight * flight);
            if (y > impact) y = impact;
            if (t < .8) {
                mist_glyph_scene_set(scene, x, y,
                                     gray_level(level_from_unit(dim_thousand / 1000.0)));
                if (y > start && y < impact)
                    mist_glyph_scene_set(scene, x, y - 1,
                        gray_level(level_from_unit(dim_thousand / 1000.0 * .28)));
            } else {
                const double fade = (t - .8) / .2;
                const int variation = (x * 73 + impact * 37) % 101;
                const double base = surface[x - MIST_GLYPH_GRID_MIN] < 28
                    ? dim_thousand / 1000.0 * (.5 + variation / 500.0)
                    : dim_thousand / 1000.0 * .5;
                const double value = dim_thousand / 1000.0 * (1.0 - fade) +
                                     base * fade;
                mist_glyph_scene_set(scene, x, impact,
                                     gray_level(level_from_unit(value)));
            }
        }
    }
}

static void build_timer(mist_glyph_page_id_t page,
                        const mist_glyph_page_context_t *context,
                        mist_glyph_scene_t *scene)
{
    if (context->minutes == 0 && context->seconds == 0 &&
        context->total_minutes > 0) {
        draw_sand(context, 280, false, scene);
        draw_icon_centered(scene, 5, 7, gray_level(242));
        add_overlay_centered(scene, "DONE", 65, gray_level(242));
        add_overlay_centered(scene, "00:00", 85, gray_level(179));
        return;
    }
    if (context->exact_time_visible) {
        char value[6];
        add_overlay_centered(scene, "REMAIN", 32, gray_level(133));
        format_time(value, context);
        draw_text_centered(scene, value, 10, gray_level(255));
        return;
    }
    if (context->total_minutes == 0) {
        draw_text_centered(scene, "?", 10, gray_level(128));
        return;
    }
    draw_sand(context, page == MIST_PAGE_TIMER_PAUSE ? 380u : 1000u,
              page == MIST_PAGE_TIMER_RUN, scene);
    if (page == MIST_PAGE_TIMER_PAUSE) {
        scene_box(scene, 9, 7, 2, 5, gray_level(222));
        scene_box(scene, 14, 7, 2, 5, gray_level(222));
        add_overlay_centered(scene, "PAUSED", 75, gray_level(171));
    }
}

static void build_timer_cancel(const mist_glyph_page_context_t *context,
                               mist_glyph_scene_t *scene)
{
    draw_sand(context, 200, false, scene);
    draw_text_centered(scene, "?", 4, gray_level(217));
    add_overlay_centered(scene, "STOP?", 58, gray_level(230));
    draw_choices(scene, context->confirm_selected, "KEEP", "STOP", 85);
}

static void build_prompt(const mist_glyph_page_context_t *context,
                         mist_glyph_scene_t *scene)
{
    const int selected = context->selected % 4;
    static const char *const glyphs[] = {
        "010110010010111", "110001010100111",
        "110001010001110", "101101111001001",
    };
    static const int8_t centers[][2] = {{12, 2}, {22, 12}, {12, 22}, {2, 12}};
    for (int y = MIST_GLYPH_GRID_MIN; y <= MIST_GLYPH_GRID_MAX; ++y) {
        for (int x = MIST_GLYPH_GRID_MIN; x <= MIST_GLYPH_GRID_MAX; ++x) {
            const int dx = x - 12;
            const int dy = y - 12;
            const double radius = hypot(dx, dy);
            double angle = atan2(dx, -dy) + M_PI * 2.0;
            angle = fmod(angle, M_PI * 2.0);
            const int sector = ((int)floor(angle / (M_PI / 2.0) + .5)) % 4;
            const double center = sector * M_PI / 2.0;
            const double delta = fabs(atan2(sin(angle - center), cos(angle - center)));
            if (radius < 6.5 || radius > 14.0 || delta >= .66) continue;
            const bool outer = radius > 12.8;
            const unsigned gain = sector == selected ? (outer ? 140u : 56u)
                                                     : (outer ? 41u : 18u);
            mist_glyph_scene_set(scene, x, y, gray_level(gain));
        }
    }
    for (int index = 0; index < 4; ++index) {
        const int cx = centers[index][0];
        const int cy = centers[index][1];
        for (int row = -2; row <= 2; ++row) for (int column = -1; column <= 1; ++column) {
            const bool lit = glyphs[index][(row + 2) * 3 + column + 1] == '1';
            const unsigned gain = lit ? (index == selected ? 250u : 110u)
                                      : (index == selected ? 56u : 18u);
            mist_glyph_scene_set(scene, cx + column, cy + row, gray_level(gain));
        }
    }
    static const int8_t directions[][2] = {{0, -1}, {1, 0}, {0, 1}, {-1, 0}};
    static const unsigned direction_gains[] = {77, 140, 204};
    for (int step = 0; step < 3; ++step)
        mist_glyph_scene_set(scene, 12 + directions[selected][0] * step,
                             12 + directions[selected][1] * step,
                             gray_level(direction_gains[step]));
}

static void build_ble(const mist_glyph_page_context_t *context,
                      mist_glyph_scene_t *scene, bool progress)
{
    if (progress) {
        add_overlay_centered(scene, "REPLACE", 25, gray_level(173));
        char value[4];
        snprintf(value, sizeof(value), "%u", context->slot >= 1 && context->slot <= 3
                                             ? context->slot : 1);
        draw_text_centered(scene, value, 9, gray_level(204));
        const int cells = (context->progress_percent * 15 + 50) / 100;
        for (int x = 5; x < 20; ++x)
            mist_glyph_scene_set(scene, x, 17,
                gray_level(x - 5 < cells ? 217u : 31u));
        char hold[8];
        snprintf(hold, sizeof(hold), "%u%%", context->progress_percent > 100
                                              ? 100 : context->progress_percent);
        add_overlay_centered(scene, hold, 100, gray_level(191));
    } else {
        const unsigned state = context->state == MIST_STATE_ACTIVE ? 2u
            : context->state == MIST_STATE_ERROR ? 0u : 1u;
        draw_icon_centered(scene, 26, 8, gray_level(state == 2 ? 255u
                                                   : state == 1 ? 140u : 46u));
        if (state == 0) scene_line(scene, 9, 8, 15, 12, gray_level(128));
        char host[10];
        snprintf(host, sizeof(host), "HOST %u", context->slot);
        add_overlay_centered(scene, host, 79, gray_level(204));
        add_overlay_centered(scene, state == 2 ? "LINKED"
                                   : state == 1 ? "PAIRED" : "EMPTY",
                             97, gray_level(148));
        draw_marks_row(scene, 3, context->slot >= 1 && context->slot <= 3
                                  ? context->slot - 1 : 0, 26);
    }
}

static void build_notice(mist_glyph_page_id_t page,
                         const mist_glyph_page_context_t *context,
                         mist_glyph_scene_t *scene)
{
    static const uint8_t resources[] = {5, 11, 7, 8, 9, 10};
    if (page >= MIST_PAGE_SUCCESS && page <= MIST_PAGE_CANCEL) {
        if (page == MIST_PAGE_WARNING && context->timer_done) {
            mist_glyph_page_context_t done = *context;
            done.minutes = 0;
            done.seconds = 0;
            if (done.total_minutes == 0) done.total_minutes = 25;
            build_timer(MIST_PAGE_TIMER_RUN, &done, scene);
            return;
        }
        const uint8_t resource = resources[page - MIST_PAGE_SUCCESS];
        draw_icon_centered(scene, resource, page == MIST_PAGE_WARNING ? 11 : 10,
                           gray_level(242));
        static const char *const labels[] = {
            "SAVED", "ERROR", "CHECK", "PLAY", "PAUSED", "CANCEL",
        };
        add_overlay_centered(scene, labels[page - MIST_PAGE_SUCCESS], 98,
                             gray_level(191));
    } else if (page == MIST_PAGE_RESTART) {
        const double t = context->animation_phase == 0u ? 1.0
            : context->animation_phase / 255.0;
        const bool active = context->animation_phase > 0u &&
                            context->animation_phase < 255u;
        for (int row = 0; row < 3; ++row) for (int column = 0; column < 3; ++column) {
            const double delay = hypot(column - 1, row - 1) * .11;
            double pulse = 0.0;
            static const double starts[] = {.02, .48};
            for (unsigned index = 0; index < 2; ++index) {
                const double local = (t - starts[index] - delay) / .28;
                if (local > 0.0 && local < 1.0) {
                    const double value = pow(sin(M_PI * local), 2.0);
                    if (value > pulse) pulse = value;
                }
            }
            scene_box(scene, 6 + column * 5, 4 + row * 5, 3, 3,
                      gray_level(level_from_unit(.22 + (active ? .73 * pulse : 0.0))));
        }
        add_overlay_centered(scene, "RESTART", 86, gray_level(179));
    } else {
        const double t = context->animation_phase == 0u ? 1.0
            : context->animation_phase / 255.0;
        const double inverse = 1.0 - t;
        const double radius = 4.0 + 6.0 * inverse * inverse * inverse;
        for (int x = (int)ceil(12.0 - radius); x <= (int)floor(12.0 + radius); ++x) {
            const double distance = fabs(x - 12.0) / radius;
            const double gain = .12 + .74 * pow(1.0 - distance, 2.0);
            mist_glyph_scene_set(scene, x, 10,
                                 gray_level(level_from_unit(gain)));
        }
        add_overlay_centered(scene, "POWER", 81, gray_level(166));
        add_overlay_centered(scene, "OFF", 101, gray_level(166));
    }
}

static const char *without_value_prefix(const char *text)
{
    return text != NULL && strncmp(text, "VALUE ", 6) == 0 ? text + 6 : text;
}

static void build_quick(const mist_glyph_page_context_t *context,
                        mist_glyph_scene_t *scene)
{
    scene_line(scene, 8, 5, 16, 5, gray_level(33));
    scene_line(scene, 6, 7, 18, 7, gray_level(69));
    scene_line(scene, 6, 17, 18, 17, gray_level(69));
    scene_line(scene, 8, 19, 16, 19, gray_level(33));
    add_overlay_centered(scene, context->title != NULL ? context->title : "PROFILE",
                         44, gray_level(242));
    add_overlay_centered(scene,
        without_value_prefix(context->primary != NULL ? context->primary : "VALUE DEFAULT"),
        65, gray_level(173));
    add_overlay_centered(scene, "APPLY", 103, gray_level(153));
}

static void build_status(const mist_glyph_page_context_t *context,
                         mist_glyph_scene_t *scene)
{
    if (context->link == MIST_LINK_NONE) {
        scene_line(scene, 8, 12, 10, 12, gray_level(89));
        scene_line(scene, 14, 12, 16, 12, gray_level(89));
        add_overlay_centered(scene, "OFF", 96, gray_level(128));
        return;
    }
    const bool connected = context->state == MIST_STATE_ACTIVE;
    const bool active = connected && context->animation_phase > 0u &&
                        context->animation_phase < 255u;
    const double t = context->animation_phase / 255.0;
    const double settle = 1.0 - pow(1.0 - t, 3.0);
    const int center_y = active ? 11 + (int)lround(1.0 - settle) : 11;
    const double gain = connected ? (active ? .35 + .6 * settle : .95) : .25;
    draw_icon_centered(scene, context->link == MIST_LINK_BLE ? 26 : 25,
                       center_y, gray_level(level_from_unit(gain)));
    add_overlay_centered(scene, context->state == MIST_STATE_ACTIVE
        ? (context->link == MIST_LINK_BLE ? "BLE" : "USB") : "WAIT",
        105, gray_level(context->state == MIST_STATE_ACTIVE ? 179u : 102u));
}

static void build_two_choice_text(const mist_glyph_page_context_t *context,
                                  mist_glyph_scene_t *scene)
{
    add_overlay_centered(scene, context->title != NULL ? context->title : "RESTART DEVICE",
                         24, gray_level(153));
    add_overlay_centered(scene, context->primary != NULL ? context->primary : "CANCEL",
                         51, gray_level(context->confirm_selected ? 77u : 242u));
    add_overlay_centered(scene, context->secondary != NULL ? context->secondary : "RESTART",
                         77, gray_level(context->confirm_selected ? 242u : 77u));
    scene_line(scene, 7, context->confirm_selected ? 21 : 14, 17,
               context->confirm_selected ? 21 : 14, gray_level(179));
}

static void build_local_text(const mist_glyph_page_context_t *context,
                             mist_glyph_scene_t *scene)
{
    add_overlay_centered(scene, context->title != NULL ? context->title : "SYSTEM",
                         25, gray_level(128));
    add_overlay_centered(scene, context->primary != NULL ? context->primary : "RESTARTING",
                         49, gray_level(242));
    if (context->secondary != NULL && context->secondary[0] != '\0')
        add_overlay_centered(scene, context->secondary, 74, gray_level(166));
    if (context->footer != NULL)
        add_overlay_centered(scene, context->footer, 98, gray_level(128));
}

bool mist_glyph_page_build(mist_glyph_page_id_t page,
                           const mist_glyph_page_context_t *context,
                           mist_glyph_scene_t *scene)
{
    if (page < 0 || page >= MIST_PAGE_COUNT || context == NULL || scene == NULL)
        return false;
    mist_glyph_scene_clear(scene, page == MIST_PAGE_SLEEP ? 0 : MIST_GLYPH_GRID_DIM);
    switch (page) {
    case MIST_PAGE_HOME: build_home(context, scene); break;
    case MIST_PAGE_BOOT: build_home(context, scene); break;
    case MIST_PAGE_BATTERY: build_battery(context, scene); break;
    case MIST_PAGE_FUNCTION:
    case MIST_PAGE_SETTINGS:
    case MIST_PAGE_SYSTEM:
    case MIST_PAGE_ALL_CAROUSEL:
    case MIST_PAGE_SYSTEM_TEXT: build_carousel(page, context, scene); break;
    case MIST_PAGE_HAPTIC: build_haptic(context, scene); break;
    case MIST_PAGE_LIGHTING: build_lighting(context, scene); break;
    case MIST_PAGE_STANDBY: build_standby(context, scene); break;
    case MIST_PAGE_RESTART_CONFIRM: build_restart_choice(context, scene); break;
    case MIST_PAGE_TIMER_CANCEL: build_timer_cancel(context, scene); break;
    case MIST_PAGE_BLE_CONFIRM: build_ble_choice(context, scene); break;
    case MIST_PAGE_TIMER_SETUP: build_timer_setup(context, scene); break;
    case MIST_PAGE_TIMER_RUN:
    case MIST_PAGE_TIMER_PAUSE: build_timer(page, context, scene); break;
    case MIST_PAGE_PROMPT: build_prompt(context, scene); break;
    case MIST_PAGE_BLE: build_ble(context, scene, false); break;
    case MIST_PAGE_BLE_PROGRESS: build_ble(context, scene, true); break;
    case MIST_PAGE_SUCCESS:
    case MIST_PAGE_ERROR:
    case MIST_PAGE_WARNING:
    case MIST_PAGE_PLAY:
    case MIST_PAGE_PAUSE:
    case MIST_PAGE_CANCEL:
    case MIST_PAGE_RESTART:
    case MIST_PAGE_SHUTDOWN: build_notice(page, context, scene); break;
    case MIST_PAGE_SLEEP: break;
    case MIST_PAGE_BMR: draw_icon_centered(scene, context->selected % 27, 13,
                                           MIST_GLYPH_WHITE); break;
    case MIST_PAGE_ICON_CHOICE: build_icon_choice(context, scene); break;
    case MIST_PAGE_STATUS_TEXT: build_status(context, scene); break;
    case MIST_PAGE_QUICK: build_quick(context, scene); break;
    case MIST_PAGE_TWO_CHOICE_TEXT: build_two_choice_text(context, scene); break;
    case MIST_PAGE_LOCAL_TEXT: build_local_text(context, scene); break;
    default: return false;
    }
    return true;
}
