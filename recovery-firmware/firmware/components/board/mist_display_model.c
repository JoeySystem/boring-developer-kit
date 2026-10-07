#include "mist_display_model.h"

#include <string.h>

static bool same_text(const char *left, const char *right)
{
    return left == right || (left != NULL && right != NULL && strcmp(left, right) == 0);
}

static bool same_context(const mist_glyph_page_context_t *a,
                         const mist_glyph_page_context_t *b)
{
    /* animation_phase belongs to the display clock, not repeated requests. */
    return a->mode == b->mode && a->link == b->link && a->state == b->state &&
        a->battery_percent == b->battery_percent && a->charging == b->charging &&
        a->selected == b->selected && a->previous == b->previous &&
        a->item_count == b->item_count && a->level == b->level &&
        a->max_level == b->max_level && a->slot == b->slot &&
        a->progress_percent == b->progress_percent && a->minutes == b->minutes &&
        a->seconds == b->seconds && a->total_minutes == b->total_minutes &&
        a->confirm_selected == b->confirm_selected && a->timer_done == b->timer_done &&
        a->exact_time_visible == b->exact_time_visible &&
        same_text(a->title, b->title) && same_text(a->primary, b->primary) &&
        same_text(a->secondary, b->secondary) && same_text(a->footer, b->footer);
}

static void save_context(mist_display_model_t *model,
                          const mist_glyph_page_context_t *context)
{
    const char *source[] = {context->title, context->primary,
                            context->secondary, context->footer};
    model->context = *context;
    const char **target[] = {&model->context.title, &model->context.primary,
                             &model->context.secondary, &model->context.footer};
    for (unsigned i = 0; i < 4; ++i) {
        if (source[i] == NULL) { *target[i] = NULL; continue; }
        if (source[i] != model->text[i]) {
            strncpy(model->text[i], source[i], sizeof(model->text[i]) - 1);
            model->text[i][sizeof(model->text[i]) - 1] = '\0';
        }
        *target[i] = model->text[i];
    }
}

static bool is_timer_page(mist_glyph_page_id_t page)
{
    return page == MIST_PAGE_TIMER_RUN || page == MIST_PAGE_TIMER_PAUSE ||
           page == MIST_PAGE_TIMER_CANCEL;
}

static bool is_native_signal(const mist_display_model_t *model)
{
    if (model->page == MIST_PAGE_BATTERY)
        return model->context.charging;
    return model->page == MIST_PAGE_RESTART ||
           model->page == MIST_PAGE_SHUTDOWN ||
           (model->page == MIST_PAGE_STATUS_TEXT &&
            model->context.state == MIST_STATE_ACTIVE &&
            model->context.link != MIST_LINK_NONE);
}

static uint16_t native_signal_duration(const mist_display_model_t *model)
{
    if (model->page == MIST_PAGE_SHUTDOWN ||
        model->page == MIST_PAGE_STATUS_TEXT) return 700;
    return 2400;
}

static float carousel_position(const mist_display_model_t *model, uint32_t now_ms)
{
    const uint32_t elapsed = now_ms - model->motion.started_at_ms;
    const float t = elapsed >= model->motion.duration_ms ? 1.0f
        : (float)elapsed / model->motion.duration_ms;
    const float eased = t * t * (3.0f - 2.0f * t);
    return model->carousel_from + (model->carousel_target - model->carousel_from) * eased;
}

static mist_glyph_motion_profile_t event_profile(mist_glyph_page_id_t page,
                                                  const mist_glyph_page_context_t *context,
                                                  mist_display_event_t event,
                                                  bool same_page)
{
    mist_glyph_motion_profile_t profile = mist_glyph_motion_profile(page);
    if (same_page && (event == MIST_DISPLAY_EVENT_DEFAULT ||
                      event == MIST_DISPLAY_EVENT_NEXT ||
                      event == MIST_DISPLAY_EVENT_PREVIOUS)) {
        if (page == MIST_PAGE_FUNCTION || page == MIST_PAGE_SYSTEM)
            return (mist_glyph_motion_profile_t){MIST_MOTION_GATHER, 560};
        if (page == MIST_PAGE_SETTINGS || page == MIST_PAGE_ALL_CAROUSEL)
            return (mist_glyph_motion_profile_t){MIST_MOTION_SCATTER, 280};
    }
    if (page == MIST_PAGE_BATTERY && context != NULL && context->charging &&
        event == MIST_DISPLAY_EVENT_DEFAULT)
        return (mist_glyph_motion_profile_t){MIST_MOTION_ADJUST, 2400};
    if (page == MIST_PAGE_STATUS_TEXT && context != NULL &&
        context->state == MIST_STATE_ACTIVE && context->link != MIST_LINK_NONE &&
        event == MIST_DISPLAY_EVENT_DEFAULT)
        return (mist_glyph_motion_profile_t){MIST_MOTION_NATIVE, 700};
    if (event == MIST_DISPLAY_EVENT_PARAMETER)
        return (mist_glyph_motion_profile_t){MIST_MOTION_ADJUST, 220};
    if (event == MIST_DISPLAY_EVENT_RETURN)
        return (mist_glyph_motion_profile_t){MIST_MOTION_GATHER, 300};
    if (event == MIST_DISPLAY_EVENT_ERROR)
        return (mist_glyph_motion_profile_t){MIST_MOTION_SCATTER, 460};
    if (event == MIST_DISPLAY_EVENT_POWER_OFF)
        return (mist_glyph_motion_profile_t){MIST_MOTION_NONE, 0};
    return profile;
}

void mist_display_model_init(mist_display_model_t *model,
                             mist_glyph_page_id_t page,
                             const mist_glyph_page_context_t *context,
                             uint32_t now_ms)
{
    if (model == NULL || context == NULL) return;
    memset(model, 0, sizeof(*model));
    model->page = page;
    save_context(model, context);
    model->timer_updated_at_ms = now_ms;
    mist_glyph_scene_clear(&model->current, MIST_GLYPH_GRID_DIM);
    model->initialized = mist_glyph_page_build(page, &model->context, &model->motion.target);
    if (model->initialized) {
        const mist_glyph_motion_profile_t profile =
            event_profile(page, context, MIST_DISPLAY_EVENT_DEFAULT, false);
        mist_glyph_motion_start(&model->motion, &model->current,
                                &model->motion.target, profile.family,
                                profile.duration_ms, now_ms);
        if (!model->motion.active) model->current = model->motion.target;
    }
    model->dirty = model->initialized;
}

bool mist_display_model_request(mist_display_model_t *model,
                                mist_glyph_page_id_t page,
                                const mist_glyph_page_context_t *context,
                                mist_display_event_t event,
                                uint32_t now_ms)
{
    if (model == NULL || context == NULL) return false;
    if (!model->initialized) {
        mist_display_model_init(model, page, context, now_ms);
        return model->initialized;
    }
    if (page == model->page && same_context(&model->context, context)) return true;
    const bool same_timer = page == model->page && is_timer_page(page);
    if (model->motion.active)
        (void)mist_display_model_frame(model, now_ms, &model->current);
    const bool carousel = page == MIST_PAGE_FUNCTION && model->page == page &&
        context->selected != model->context.selected &&
        (event == MIST_DISPLAY_EVENT_DEFAULT || event == MIST_DISPLAY_EVENT_NEXT ||
         event == MIST_DISPLAY_EVENT_PREVIOUS);
    if (carousel) {
        const float from = model->carousel_active
            ? carousel_position(model, now_ms) : model->context.selected;
        const float target = model->carousel_active
            ? model->carousel_target : model->context.selected;
        const int direction = event == MIST_DISPLAY_EVENT_PREVIOUS ? -1
            : event == MIST_DISPLAY_EVENT_NEXT ? 1
            : context->selected > model->context.selected ? 1 : -1;
        model->carousel_from = from;
        model->carousel_target = target + direction;
    }
    model->carousel_active = carousel;
    const uint8_t timer_phase = is_timer_page(model->page)
        ? model->context.animation_phase : 0;
    save_context(model, context);
    if (is_timer_page(page)) model->context.animation_phase = timer_phase;
    if (!mist_glyph_page_build(page, &model->context, &model->motion.target))
        return false;
    if (same_timer) {
        if (!model->motion.active) model->current = model->motion.target;
        model->dirty = true;
        return true;
    }
    const mist_glyph_motion_profile_t profile =
        event_profile(page, context, event, model->page == page);
    mist_glyph_motion_start(&model->motion, &model->current,
                            &model->motion.target, profile.family,
                            profile.duration_ms, now_ms);
    if (!model->motion.active)
        model->current = model->motion.target;
    model->page = page;
    model->timer_updated_at_ms = now_ms;
    model->dirty = true;
    return true;
}

bool mist_display_model_frame(mist_display_model_t *model, uint32_t now_ms,
                              mist_glyph_scene_t *scene)
{
    if (model == NULL || scene == NULL || !model->initialized) return false;
    if (model->page == MIST_PAGE_TIMER_RUN && model->context.total_minutes > 0 &&
        (model->context.minutes > 0 || model->context.seconds > 0)) {
        const uint32_t steps = (now_ms - model->timer_updated_at_ms) / 50u;
        if (steps > 0) {
            model->timer_updated_at_ms += steps * 50u;
            model->context.animation_phase =
                (uint8_t)((model->context.animation_phase + steps) % 20u);
            if (!model->context.exact_time_visible) {
                (void)mist_glyph_page_build(model->page, &model->context, &model->motion.target);
                if (!model->motion.active) model->current = model->motion.target;
                model->dirty = true;
            }
        }
    }
    if (!model->dirty && !model->motion.active) return false;
    if (model->motion.active) {
        if (model->carousel_active) {
            mist_glyph_function_carousel_frame(&model->context,
                                                carousel_position(model, now_ms),
                                                &model->current);
        } else if (is_native_signal(model)) {
            const uint16_t duration = native_signal_duration(model);
            const uint32_t elapsed = now_ms - model->motion.started_at_ms;
            mist_glyph_page_context_t context = model->context;
            if (elapsed >= duration) {
                context.animation_phase = 255u;
            } else {
                const uint32_t phase = (elapsed * 255u + duration / 2u) / duration;
                context.animation_phase = (uint8_t)(phase == 0u ? 1u : phase);
            }
            (void)mist_glyph_page_build(model->page, &context, &model->current);
        } else {
            (void)mist_glyph_motion_compose(&model->motion, now_ms,
                                            &model->current);
        }
        if (mist_glyph_motion_finished(&model->motion, now_ms)) {
            model->motion.active = false;
            model->carousel_active = false;
            if (!is_timer_page(model->page)) model->context.animation_phase = 255u;
        }
    }
    if (scene != &model->current) *scene = model->current;
    model->dirty = false;
    return true;
}

void mist_display_model_cancel(mist_display_model_t *model,
                               mist_glyph_page_id_t terminal_page,
                               const mist_glyph_page_context_t *context)
{
    if (model == NULL || context == NULL) return;
    model->page = terminal_page;
    save_context(model, context);
    model->motion.active = false;
    model->carousel_active = false;
    model->initialized = mist_glyph_page_build(terminal_page, &model->context,
                                               &model->current);
    model->dirty = model->initialized;
}

void mist_display_model_finish(mist_display_model_t *model)
{
    if (model == NULL || !model->initialized) return;
    if (model->motion.active) model->current = model->motion.target;
    model->motion.active = false;
    model->carousel_active = false;
    model->dirty = false;
}
