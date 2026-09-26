#include "board_mist_ui.h"
#include "mist_display_model.h"
#include "screen_icon_store.h"
#include "screen_glyph_store.h"

#include <stdio.h>
#include <stdatomic.h>
#include <string.h>

#ifdef ESP_PLATFORM
#include "esp_attr.h"
#else
#define EXT_RAM_BSS_ATTR
#endif

/* Application values enter here; Glyph owns geometry, assets and animation. */
typedef struct {
    mist_glyph_page_id_t page;
    mist_glyph_page_context_t context;
    char text[4][MIST_GLYPH_OVERLAY_TEXT_LENGTH];
    uint16_t part_count;
} mist_request_t;

static mist_display_model_t s_mist_display;
static bool s_mist_suspended;
static bool s_mist_explicit_phase;
/* Display-owned snapshot: never retain store memory or read flash per stripe. */
static EXT_RAM_BSS_ATTR uint8_t s_home_icon[SCREEN_ICON_TOTAL_BYTES];
static screen_icon_metadata_t s_home_icon_metadata;
static uint32_t s_glyph_epoch = UINT32_MAX;
static EXT_RAM_BSS_ATTR screen_glyph_record_t s_glyph_records[SCREEN_GLYPH_COUNT];
/* One atomic value keeps both percentages from ever coming from different updates. */
static atomic_uint s_codex_usage;
static atomic_uint s_codex_usage_expires_at;
static unsigned s_rendered_codex_usage;
static bool s_codex_usage_visible;

#define CODEX_USAGE_LEASE_MS 600000u

void board_mist_ui_set_codex_usage(uint8_t weekly, uint8_t five_hour,
                                   uint32_t now_ms)
{
    atomic_store(&s_codex_usage_expires_at, now_ms + CODEX_USAGE_LEASE_MS);
    atomic_store(&s_codex_usage, 0x10000u | ((unsigned)five_hour << 8) | weekly);
}

void board_mist_ui_clear_codex_usage(void)
{
    atomic_store(&s_codex_usage, 0);
}

static bool codex_usage_active(unsigned value, uint32_t now_ms)
{
    return (value & 0x10000u) != 0 &&
        (int32_t)(now_ms - atomic_load(&s_codex_usage_expires_at)) < 0;
}

static void render_codex_usage(unsigned value)
{
    char weekly[16], five_hour[16];
    const unsigned weekly_percent = value & 0xffu;
    const unsigned five_hour_percent = (value >> 8) & 0xffu;
    if (weekly_percent <= 100u)
        snprintf(weekly, sizeof(weekly), "7D  %u%%", weekly_percent);
    else snprintf(weekly, sizeof(weekly), "7D  --");
    if (five_hour_percent <= 100u)
        snprintf(five_hour, sizeof(five_hour), "5H  %u%%", five_hour_percent);
    else snprintf(five_hour, sizeof(five_hour), "5H  --");
    mist_glyph_page_context_t context = {
        .title = "CODEX LEFT",
        .primary = weekly,
        .secondary = five_hour,
    };
    (void)mist_glyph_page_build(MIST_PAGE_LOCAL_TEXT, &context,
                                &s_mist_display.current);
    s_rendered_codex_usage = value;
    s_codex_usage_visible = true;
}

static void refresh_glyphs(void)
{
    if (!screen_glyph_store_try_snapshot(&s_glyph_epoch, s_glyph_records)) return;
    for (unsigned i = 0; i < SCREEN_GLYPH_COUNT; ++i)
        mist_glyph_icon_override(i, s_glyph_records[i].custom ? s_glyph_records[i].pixels : NULL);
    if (s_mist_display.initialized && !s_mist_explicit_phase) {
        (void)mist_glyph_page_build(s_mist_display.page, &s_mist_display.context,
                                   &s_mist_display.motion.target);
        if (!s_mist_display.motion.active)
            s_mist_display.current = s_mist_display.motion.target;
    }
    s_mist_display.dirty = true;
}

static bool normal_idle_home(void)
{
    return s_mist_display.initialized && !s_mist_suspended &&
        !s_mist_explicit_phase && s_mist_display.page == MIST_PAGE_HOME &&
        s_mist_display.context.mode == MIST_MODE_NORMAL &&
        !s_mist_display.motion.active;
}

static void refresh_home_icon(void)
{
    const screen_icon_metadata_t previous = s_home_icon_metadata;
    /* A busy NVS writer must not stall physical input or blank the old image. */
    if (!screen_icon_store_try_snapshot(&s_home_icon_metadata,
                                       s_home_icon, sizeof(s_home_icon))) return;
    if (normal_idle_home() &&
        (previous.revision != s_home_icon_metadata.revision ||
         previous.custom != s_home_icon_metadata.custom))
        s_mist_display.dirty = true;
}

static uint8_t byte_value(int value)
{
    return value < 0 ? 0 : value > 255 ? 255 : (uint8_t)value;
}

static uint8_t signal_phase(float phase)
{
    if (phase >= 1) return 255;
    if (phase <= 0) return 1; /* Zero means terminal frame in the donor renderer. */
    const unsigned value = (unsigned)(phase * 255.0f + .5f);
    return (uint8_t)(value ? value : 1);
}

static mist_glyph_mode_t home_mode(int mode)
{
    switch (mode) {
    case 1: return MIST_MODE_CODEX;
    case 2: return MIST_MODE_CLAUDE_CODE;
    default: return MIST_MODE_NORMAL;
    }
}

static mist_glyph_state_t setting_state(int state)
{
    return state == 1 ? MIST_STATE_APPLYING :
           state == 2 ? MIST_STATE_ERROR : MIST_STATE_IDLE;
}

/* Preserve one fallback glyph per unsupported UTF-8 character. */
static char next_character(const char **text)
{
    if (text == NULL || *text == NULL || **text == '\0') return '\0';
    const unsigned char *cursor = (const unsigned char *)*text;
    unsigned char value = *cursor++;
    if (value >= 128) {
        while ((*cursor & 0xc0) == 0x80) ++cursor;
        value = '?';
    }
    *text = (const char *)cursor;
    return value >= 'a' && value <= 'z' ? (char)(value - 'a' + 'A') : (char)value;
}

static unsigned text_chunks(const char *text, unsigned selected, char *output)
{
    unsigned count = 0, length = 0;
    int width = -1;
    bool any = false;
    if (output != NULL) output[0] = '\0';
    char value;
    while ((value = next_character(&text))) {
        const mist_glyph_font_glyph_t *glyph = mist_glyph_font_find(value);
        if (glyph == NULL) glyph = mist_glyph_font_find('?');
        const int advance = glyph->width + 1;
        if (width + advance > 19) { ++count; width = -1; length = 0; }
        width += advance;
        if (count == selected && output != NULL &&
            length + 1 < MIST_GLYPH_OVERLAY_TEXT_LENGTH) {
            output[length] = value;
            output[length + 1] = '\0';
        }
        ++length;
        any = true;
    }
    return any ? count + 1 : 0;
}

static const char *request_text(mist_request_t *request, unsigned index,
                                const char *text)
{
    if (text == NULL) return NULL;
    unsigned length = 0;
    char value;
    while (length + 1 < sizeof(request->text[index]) &&
           (value = next_character(&text))) request->text[index][length++] = value;
    request->text[index][length] = '\0';
    return request->text[index];
}

typedef struct { const char *label; const char *value; } read_entry_t;

static unsigned read_entries(const board_mist_view_t *view, read_entry_t entries[6])
{
    switch (view->page) {
    case BOARD_MIST_QUICK:
        entries[0] = (read_entry_t){"ITEM", view->title};
        entries[1] = (read_entry_t){"VALUE", view->primary};
        entries[2] = (read_entry_t){"SAVED", view->saved};
        entries[3] = (read_entry_t){"PREV", view->previous};
        entries[4] = (read_entry_t){"NEXT", view->next};
        entries[5] = (read_entry_t){"HINT", view->footer}; return 6;
    case BOARD_MIST_CONNECTION:
        entries[0] = (read_entry_t){"CFG", view->title};
        entries[1] = (read_entry_t){"LINK", view->primary};
        entries[2] = (read_entry_t){"MODE", view->secondary};
        entries[3] = (read_entry_t){"HINT", view->footer}; return 4;
    case BOARD_MIST_LOCAL:
        entries[0] = (read_entry_t){"TITLE", view->title};
        entries[1] = (read_entry_t){"MAIN", view->primary};
        entries[2] = (read_entry_t){"MORE", view->secondary};
        entries[3] = (read_entry_t){"HINT", view->footer}; return 4;
    case BOARD_MIST_TWO_CHOICE:
        entries[0] = (read_entry_t){"PICK", view->a == 0 ? view->primary : view->secondary};
        entries[1] = (read_entry_t){"TITLE", view->title};
        entries[2] = (read_entry_t){"OTHER", view->a == 0 ? view->secondary : view->primary};
        entries[3] = (read_entry_t){"HINT", view->footer}; return 4;
    case BOARD_MIST_SYSTEM_TEXT:
        entries[0] = (read_entry_t){"OS", view->a == 0 ? "MAC" : "WIN / LINUX"};
        entries[1] = (read_entry_t){"HINT", view->footer}; return 2;
    default: return 0;
    }
}

static bool read_details(mist_request_t *request, const board_mist_view_t *view)
{
    read_entry_t entries[6];
    const unsigned count = read_entries(view, entries);
    unsigned pages = 0;
    for (unsigned i = 0; i < count; ++i) pages += text_chunks(entries[i].value, 0, NULL);
    request->part_count = (uint16_t)(pages + 1);
    if (view->read_index == 0 || pages == 0) return false;
    const unsigned page = (view->read_index - 1u) % pages;
    unsigned selected = page;
    for (unsigned i = 0; i < count; ++i) {
        const unsigned parts = text_chunks(entries[i].value, 0, NULL);
        if (selected >= parts) { selected -= parts; continue; }
        request->page = MIST_PAGE_LOCAL_TEXT;
        request->context.title = request_text(request, 0, entries[i].label);
        text_chunks(entries[i].value, selected, request->text[1]);
        request->context.primary = request->text[1];
        request->context.secondary = NULL;
        snprintf(request->text[3], sizeof(request->text[3]), "%u/%u", page + 1u, pages);
        request->context.footer = request->text[3];
        return true;
    }
    return false;
}

static mist_glyph_page_id_t notice_page(int icon)
{
    switch (icon) {
    case BOARD_MIST_ICON_CONFIRM: return MIST_PAGE_SUCCESS;
    case BOARD_MIST_ICON_ERROR: return MIST_PAGE_ERROR;
    case BOARD_MIST_ICON_WARNING: return MIST_PAGE_WARNING;
    case BOARD_MIST_ICON_PLAY: return MIST_PAGE_PLAY;
    case BOARD_MIST_ICON_PAUSE: return MIST_PAGE_PAUSE;
    case BOARD_MIST_ICON_CANCEL: return MIST_PAGE_CANCEL;
    case BOARD_MIST_ICON_RESTART: return MIST_PAGE_RESTART;
    case BOARD_MIST_ICON_POWER: return MIST_PAGE_SHUTDOWN;
    default: return MIST_PAGE_BMR;
    }
}

static bool map_request(mist_request_t *request, const board_mist_view_t *view)
{
    if (request == NULL || view == NULL || view->page < 0 ||
        view->page >= BOARD_MIST_PAGE_COUNT) return false;
    memset(request, 0, sizeof(*request));
    mist_glyph_page_context_t *context = &request->context;
    context->animation_phase = signal_phase(view->phase);
    context->max_level = view->max_level ? view->max_level : 4;
    context->title = request_text(request, 0, view->title);
    context->primary = request_text(request, 1, view->primary);
    context->secondary = request_text(request, 2, view->secondary);
    context->footer = request_text(request, 3, view->footer);
    if (read_details(request, view)) return true;
    switch (view->page) {
    case BOARD_MIST_HOME:
        request->page = MIST_PAGE_HOME; context->mode = home_mode(view->a); break;
    case BOARD_MIST_BOOT:
        request->page = MIST_PAGE_BOOT; context->mode = home_mode(view->a); break;
    case BOARD_MIST_BATTERY:
        request->page = MIST_PAGE_BATTERY;
        context->battery_percent = view->a; context->charging = view->b != 0; break;
    case BOARD_MIST_CONNECTION:
        request->page = MIST_PAGE_STATUS_TEXT;
        context->link = view->a == 1 ? MIST_LINK_USB : view->a == 2 ? MIST_LINK_BLE : MIST_LINK_NONE;
        context->state = view->b == 2 ? MIST_STATE_ACTIVE : MIST_STATE_IDLE; break;
    case BOARD_MIST_CAROUSEL:
        context->selected = view->selected_index;
        context->item_count = view->item_count;
        if (view->item_count == 5) request->page = MIST_PAGE_SETTINGS;
        else if (view->item_count == 2 && view->items[0] == BOARD_MIST_ICON_MACOS &&
                 view->items[1] == BOARD_MIST_ICON_WINDOWS) request->page = MIST_PAGE_SYSTEM;
        else if ((view->item_count == 2 || view->item_count == 3) &&
                 view->items[0] == BOARD_MIST_ICON_FOCUS &&
                 view->items[1] == BOARD_MIST_ICON_SETTINGS) request->page = MIST_PAGE_FUNCTION;
        else { request->page = MIST_PAGE_ALL_CAROUSEL; context->selected = byte_value(view->a); }
        break;
    case BOARD_MIST_PROMPT:
        request->page = MIST_PAGE_PROMPT;
        context->selected = view->a > 0 ? (view->a - 1) % 4 : 0; break;
    case BOARD_MIST_HAPTIC:
    case BOARD_MIST_LIGHTING:
        request->page = view->page == BOARD_MIST_HAPTIC ? MIST_PAGE_HAPTIC : MIST_PAGE_LIGHTING;
        context->level = byte_value(view->a); context->state = setting_state(view->b);
        context->confirm_selected = view->c != 0; break;
    case BOARD_MIST_STANDBY:
        request->page = MIST_PAGE_STANDBY; context->minutes = byte_value(view->a);
        context->state = setting_state(view->c); context->confirm_selected = view->d != 0; break;
    case BOARD_MIST_RESTART_CONFIRM:
        request->page = MIST_PAGE_RESTART_CONFIRM; context->confirm_selected = view->confirm_selected; break;
    case BOARD_MIST_NOTICE:
        request->page = notice_page(view->a); context->selected = byte_value(view->a);
        context->timer_done = view->a == BOARD_MIST_ICON_WARNING && view->b == 1;
        context->total_minutes = byte_value(view->total_seconds / 60u); break;
    case BOARD_MIST_TIMER:
        context->minutes = byte_value(view->remaining_seconds / 60u);
        context->seconds = view->remaining_seconds % 60u;
        context->total_minutes = byte_value(view->total_seconds / 60u);
        context->exact_time_visible = view->show_exact_time && view->total_seconds > 0;
        context->confirm_selected = view->confirm_selected;
        context->animation_phase = (uint8_t)(view->phase < 0 ? 0 : (unsigned)(view->phase * 20) % 20u);
        if (view->timer_state == BOARD_MIST_TIMER_SETUP) {
            request->page = MIST_PAGE_TIMER_SETUP;
            context->minutes = byte_value(view->a); context->seconds = byte_value(view->b);
        } else if (view->timer_state == BOARD_MIST_TIMER_PAUSED) request->page = MIST_PAGE_TIMER_PAUSE;
        else if (view->timer_state == BOARD_MIST_TIMER_CANCEL) request->page = MIST_PAGE_TIMER_CANCEL;
        else {
            request->page = MIST_PAGE_TIMER_RUN;
            if (view->timer_state == BOARD_MIST_TIMER_DONE) context->minutes = context->seconds = 0;
        }
        break;
    case BOARD_MIST_QUICK: request->page = MIST_PAGE_QUICK; break;
    case BOARD_MIST_LOCAL: request->page = MIST_PAGE_LOCAL_TEXT; break;
    case BOARD_MIST_TWO_CHOICE:
        request->page = MIST_PAGE_TWO_CHOICE_TEXT; context->confirm_selected = view->a != 0; break;
    case BOARD_MIST_SYSTEM_TEXT:
        request->page = MIST_PAGE_SYSTEM_TEXT; context->selected = byte_value(view->a); break;
    case BOARD_MIST_BLE:
    case BOARD_MIST_BLE_PROGRESS:
    case BOARD_MIST_BLE_CONFIRM:
        request->page = view->page == BOARD_MIST_BLE ? MIST_PAGE_BLE :
            view->page == BOARD_MIST_BLE_PROGRESS ? MIST_PAGE_BLE_PROGRESS : MIST_PAGE_BLE_CONFIRM;
        context->slot = byte_value(view->a);
        context->state = view->b == 2 ? MIST_STATE_ACTIVE :
            view->b == 1 ? MIST_STATE_IDLE : MIST_STATE_ERROR;
        context->progress_percent = byte_value(view->c);
        context->confirm_selected = view->c != 0; break;
    case BOARD_MIST_PAGE_COUNT: return false;
    }
    return true;
}

static bool explicit_phase(const board_mist_view_t *view)
{
    return view->page == BOARD_MIST_BOOT || (view->page == BOARD_MIST_NOTICE &&
        view->a == BOARD_MIST_ICON_RESTART);
}

static void build_request_scene(const mist_request_t *request,
                                 const board_mist_view_t *view, mist_glyph_scene_t *scene)
{
    if (view->page == BOARD_MIST_BOOT) {
        const float phase = view->phase < 0 ? 0 : view->phase > 1 ? 1 : view->phase;
        (void)mist_glyph_boot_frame((uint32_t)(phase * MIST_GLYPH_BOOT_TOTAL_MS + .5f),
                                    request->context.mode, scene);
    } else (void)mist_glyph_page_build(request->page, &request->context, scene);
}

void board_mist_ui_prepare(board_mist_frame_t *frame, const board_mist_view_t *view)
{
    if (frame == NULL) return;
    mist_request_t request;
    memset(frame, 0, sizeof(*frame));
    if (!map_request(&request, view)) return;
    frame->part_count = request.part_count;
    build_request_scene(&request, view, &frame->scene);
}

void board_mist_ui_render_strip(const board_mist_frame_t *frame,
                                uint16_t *pixels, int y, int lines)
{
    if (frame != NULL) mist_glyph_render_band(&frame->scene, pixels, 128, y, lines);
}

void board_mist_ui_render_scene_strip(const mist_glyph_scene_t *scene,
                                     uint16_t *pixels, int y, int lines)
{
    if (scene != &s_mist_display.current || !normal_idle_home() ||
        s_codex_usage_visible ||
        !s_home_icon_metadata.custom) {
        mist_glyph_render_band(scene, pixels, 128, y, lines);
        return;
    }
    for (int row = 0; row < lines; ++row) {
        const int source_y = y + row;
        for (int x = 0; x < (int)SCREEN_ICON_WIDTH; ++x) {
            const int dx = 2 * x + 1 - 128, dy = 2 * source_y + 1 - 128;
            uint16_t color = 0;
            if (source_y >= 0 && source_y < (int)SCREEN_ICON_HEIGHT &&
                dx * dx + dy * dy <= 125 * 125) {
                const size_t offset = (source_y * SCREEN_ICON_WIDTH + x) * 2u;
                /* Wire RGB565 is LE; the existing SPI uint16_t buffer is swapped. */
                color = ((uint16_t)s_home_icon[offset] << 8) | s_home_icon[offset + 1];
            }
            pixels[row * SCREEN_ICON_WIDTH + x] = color;
        }
    }
}

bool board_mist_ui_request(const board_mist_view_t *view, uint32_t now_ms)
{
    mist_request_t request;
    if (!map_request(&request, view)) return false;
    refresh_glyphs();
    s_mist_explicit_phase = explicit_phase(view);
    if (s_mist_explicit_phase) {
        mist_display_model_cancel(&s_mist_display, request.page, &request.context);
        build_request_scene(&request, view, &s_mist_display.current);
    } else {
        if (view->page != BOARD_MIST_TIMER) request.context.animation_phase = 255;
        const mist_display_event_t event = request.page == MIST_PAGE_FUNCTION && view->b != 0
            ? (view->b > 0 ? MIST_DISPLAY_EVENT_NEXT : MIST_DISPLAY_EVENT_PREVIOUS)
            : MIST_DISPLAY_EVENT_DEFAULT;
        if (!mist_display_model_request(&s_mist_display, request.page, &request.context,
                                        event, now_ms)) return false;
    }
    if (s_mist_suspended) s_mist_display.dirty = true;
    s_mist_suspended = false;
    return true;
}

const mist_glyph_scene_t *board_mist_ui_frame(uint32_t now_ms)
{
    if (s_mist_suspended) return NULL;
    refresh_home_icon();
    refresh_glyphs();
    if (s_mist_explicit_phase) {
        if (!s_mist_display.dirty) return NULL;
        s_mist_display.dirty = false;
        return &s_mist_display.current;
    }
    const bool base_changed = mist_display_model_frame(
        &s_mist_display, now_ms, &s_mist_display.current);
    if (s_mist_display.page != MIST_PAGE_HOME || s_mist_display.motion.active ||
        s_mist_display.context.mode == MIST_MODE_CLAUDE_CODE) {
        s_codex_usage_visible = false;
        return base_changed ? &s_mist_display.current : NULL;
    }
    const unsigned value = atomic_load(&s_codex_usage);
    if (codex_usage_active(value, now_ms)) {
        if (base_changed || !s_codex_usage_visible ||
            value != s_rendered_codex_usage) {
            render_codex_usage(value);
            return &s_mist_display.current;
        }
        return NULL;
    }
    if (s_codex_usage_visible) {
        s_codex_usage_visible = false;
        (void)mist_glyph_page_build(MIST_PAGE_HOME, &s_mist_display.context,
                                    &s_mist_display.current);
        return &s_mist_display.current;
    }
    return base_changed ? &s_mist_display.current : NULL;
}

void board_mist_ui_finish_motion(void)
{
    mist_display_model_finish(&s_mist_display);
    s_mist_suspended = true;
}
