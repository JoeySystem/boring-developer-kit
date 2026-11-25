#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Stability item 9: ROM download mode without the BOOT key, entered only
 * from safe mode after an on-device confirmation (boot_combo_model.h). There
 * is no protocol command: without secure boot and flash encryption it would
 * let any host program read the whole flash.
 *
 * Hands the internal USB PHY back to the USB Serial/JTAG controller (the app
 * maps it to USB-OTG for TinyUSB), sets RTC_CNTL_FORCE_DOWNLOAD_BOOT and
 * restarts. The ROM then enumerates its standard download port (303A:1001)
 * for esptool. Does not return.
 */
void rom_download_enter(void) __attribute__((noreturn));

#ifdef __cplusplus
}
#endif
