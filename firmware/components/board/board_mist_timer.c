#include "board_mist_ui_internal.h"

#include <math.h>
#include <stdio.h>

/* Each column is already ordered from its bottom upwards. Merging those 31
 * columns gives the same ordering as the editor's complete-cell sort, with
 * only a small fixed heap. The display task is the sole rendering caller. */
typedef struct {
    double score;
    int8_t x;
    int8_t y;
    int8_t top;
} sand_column_t;

static sand_column_t sand_heap[BOARD_MIST_GRID_SIZE];
static int8_t sand_surface[BOARD_MIST_GRID_SIZE];
static int8_t sand_floor[BOARD_MIST_GRID_SIZE];
static uint32_t sand_total_seconds;
static uint32_t sand_remaining_seconds;

static bool column_precedes(const sand_column_t *a, const sand_column_t *b)
{
    return a->score > b->score || (a->score == b->score && a->x < b->x);
}

static void sift_down(unsigned count)
{
    unsigned index = 0;
    sand_column_t column = sand_heap[0];
    while (index * 2 + 1 < count) {
        unsigned child = index * 2 + 1;
        if (child + 1 < count && column_precedes(&sand_heap[child + 1], &sand_heap[child])) {
            ++child;
        }
        if (!column_precedes(&sand_heap[child], &column)) break;
        sand_heap[index] = sand_heap[child];
        index = child;
    }
    sand_heap[index] = column;
}

static float sand_tone(int x, int y, float dim)
{
    return dim * (0.5f + (float)((x * 73 + y * 37) % 101) / 500.0f);
}

static void prepare_sand(uint32_t total_seconds, uint32_t remaining_seconds)
{
    /* Only known, nonzero totals reach this helper. The settled pile changes
     * once per remaining second; animation phase and dimming do not change it. */
    if (total_seconds == sand_total_seconds && remaining_seconds == sand_remaining_seconds) return;
    double amount = 1.0 - (double)remaining_seconds / total_seconds;
    if (amount < 0.0) amount = 0.0;
    if (amount > 1.0) amount = 1.0;
    double relax = (amount - 0.4) / 0.25;
    if (relax < 0.0) relax = 0.0;
    if (relax > 1.0) relax = 1.0;
    const double slope = 1.25 * (1.0 - relax * relax * (3.0 - 2.0 * relax));
    unsigned heap_count = 0;
    unsigned cell_count = 0;

    for (int x = BOARD_MIST_GRID_MIN; x <= BOARD_MIST_GRID_MAX; ++x) {
        const int index = x - BOARD_MIST_GRID_MIN;
        int top = BOARD_MIST_GRID_MAX + 1;
        int bottom = BOARD_MIST_GRID_MIN - 1;
        sand_surface[index] = BOARD_MIST_GRID_MAX + 1;
        for (int y = BOARD_MIST_GRID_MIN; y <= BOARD_MIST_GRID_MAX; ++y) {
            if (!mist_grid_valid(x, y)) continue;
            if (top > y) top = y;
            bottom = y;
            ++cell_count;
        }
        sand_floor[index] = bottom;
        if (bottom < top) continue;

        const int distance = x < 12 ? 12 - x : x - 12;
        sand_column_t column = {
            .score = bottom - slope * distance,
            .x = x, .y = bottom, .top = top,
        };
        unsigned child = heap_count++;
        while (child > 0) {
            const unsigned parent = (child - 1) / 2;
            if (!column_precedes(&column, &sand_heap[parent])) break;
            sand_heap[child] = sand_heap[parent];
            child = parent;
        }
        sand_heap[child] = column;
    }

    const unsigned filled_count = (unsigned)(cell_count * amount + 0.5);
    for (unsigned filled = 0; filled < filled_count && heap_count; ++filled) {
        const sand_column_t column = sand_heap[0];
        sand_surface[column.x - BOARD_MIST_GRID_MIN] = column.y;
        if (column.y > column.top) {
            --sand_heap[0].y;
            const int distance = column.x < 12 ? 12 - column.x : column.x - 12;
            sand_heap[0].score = sand_heap[0].y - slope * distance;
        } else {
            sand_heap[0] = sand_heap[--heap_count];
        }
        if (heap_count) sift_down(heap_count);
    }
    sand_total_seconds = total_seconds;
    sand_remaining_seconds = remaining_seconds;
}

static void render_sand(board_mist_frame_t *f, uint32_t total_seconds,
                        uint32_t remaining_seconds, float phase, float dim)
{
    prepare_sand(total_seconds, remaining_seconds);
    for (int x = BOARD_MIST_GRID_MIN; x <= BOARD_MIST_GRID_MAX; ++x) {
        const int index = x - BOARD_MIST_GRID_MIN;
        for (int y = sand_floor[index]; y >= sand_surface[index]; --y) {
            mist_cell(f, x, y, sand_tone(x, y, dim));
        }
    }

    if (phase <= 0.0f || phase >= 1.0f || remaining_seconds == 0) return;
    static const int columns[] = {12, 11, 13};
    for (unsigned grain = 0; grain < 3; ++grain) {
        const int x = columns[grain];
        const int index = x - BOARD_MIST_GRID_MIN;
        const bool has_sand = sand_surface[index] <= BOARD_MIST_GRID_MAX;
        const int impact = has_sand ? sand_surface[index] : sand_floor[index];
        const int start = -2;
        if (impact <= start) continue;
        double t = phase + (double)grain / 3.0;
        if (t >= 1.0) t -= 1.0;
        if (t < 0.8) {
            const double flight = t / 0.8;
            int y = (int)floor(start + (impact - start) * flight * flight);
            if (y > impact) y = impact;
            mist_cell(f, x, y, dim);
            if (y > start && y < impact) mist_cell(f, x, y - 1, dim * 0.28f);
        } else {
            const float fade = (t - 0.8) / 0.2;
            const float base = has_sand ? sand_tone(x, impact, dim) : dim * 0.5f;
            mist_cell(f, x, impact, dim * (1.0f - fade) + base * fade);
        }
    }
}

static void render_setup(board_mist_frame_t *f, const board_mist_view_t *view)
{
    char minutes[16];
    snprintf(minutes, sizeof(minutes), "%02d", view->a);
    mist_aux(f, "TIMER", 25, 0.66f, 64, view->read_index);
    mist_text(f, minutes, 9, 0.95f);
    mist_aux(f, "MIN", 75, 0.52f, 64, view->read_index);
    const float back = view->d == 0 ? 0.9f : 0.35f;
    const float start = view->d == 1 ? 0.95f : 0.35f;
    mist_line(f, 7, 24, 9, 22, back);
    mist_line(f, 7, 24, 9, 26, back);
    mist_box(f, 15, 23, 1, 3, start);
    mist_cell(f, 16, 24, start);
}

void mist_render_timer(board_mist_frame_t *f, const board_mist_view_t *view)
{
    if (view->timer_state == BOARD_MIST_TIMER_SETUP) {
        render_setup(f, view);
        return;
    }

    const bool known_total = view->total_seconds > 0;

    if (view->timer_state == BOARD_MIST_TIMER_CANCEL) {
        if (known_total) render_sand(f, view->total_seconds, view->remaining_seconds, 0.0f, 0.2f);
        mist_text(f, "?", 4, 0.85f);
        mist_aux(f, "STOP?", 58, 0.9f, 64, view->read_index);
        mist_choices(f, view->confirm_selected, "KEEP", "STOP", 85);
        return;
    }

    if (view->timer_state == BOARD_MIST_TIMER_DONE &&
        view->remaining_seconds == 0 && known_total) {
        render_sand(f, view->total_seconds, 0, 0.0f, 0.28f);
        mist_icon(f, BOARD_MIST_ICON_CONFIRM, 12, 7, 0.95f);
        mist_aux(f, "DONE", 65, 0.95f, 64, view->read_index);
        mist_aux(f, "00:00", 85, 0.7f, 64, view->read_index);
        return;
    }

    const bool paused = view->timer_state == BOARD_MIST_TIMER_PAUSED;
    const float dim = view->show_exact_time ? 0.18f : paused ? 0.38f : 1.0f;
    /* The action engine supplies a frozen phase while paused, so the grains
     * stay at the same positions rather than vanishing on the pause frame. */
    if (known_total) render_sand(f, view->total_seconds, view->remaining_seconds, view->phase, dim);
    if (view->show_exact_time) {
        char remaining[20];
        snprintf(remaining, sizeof(remaining), "%02lu:%02lu",
                 (unsigned long)(view->remaining_seconds / 60),
                 (unsigned long)(view->remaining_seconds % 60));
        mist_aux(f, "REMAIN", 32, 0.52f, 64, view->read_index);
        mist_text(f, remaining, 10, 0.95f);
        if (!known_total) mist_aux(f, "?", 98, 0.5f, 64, view->read_index);
    } else if (paused) {
        mist_box(f, 9, 7, 2, 5, 0.87f);
        mist_box(f, 14, 7, 2, 5, 0.87f);
        mist_aux(f, "PAUSED", 75, 0.67f, 64, view->read_index);
        if (!known_total) mist_aux(f, "?", 98, 0.5f, 64, view->read_index);
    } else if (!known_total) {
        mist_text(f, "?", 10, 0.5f);
    }
}
