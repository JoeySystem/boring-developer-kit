#include "usb_connection_feedback.h"

usb_connection_feedback_t usb_connection_feedback(bool mounted)
{
    if (mounted) {
        return (usb_connection_feedback_t) {
            .label = "USB READY",
            .red = 0,
            .green = 48,
            .blue = 8,
        };
    }
    return (usb_connection_feedback_t) {
        .label = "USB OFFLINE",
        .red = 48,
        .green = 12,
        .blue = 0,
    };
}
