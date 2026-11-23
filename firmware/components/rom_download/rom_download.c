#include "rom_download.h"

#include "esp_log.h"
#include "esp_system.h"
#include "soc/rtc_cntl_reg.h"
#include "soc/rtc_cntl_struct.h"
#include "soc/soc.h"

static const char *TAG = "rom_download";

void rom_download_enter(void)
{
    ESP_LOGW(TAG, "restarting into ROM download mode (USB Serial/JTAG)");
    /* 0: internal FSLS PHY to USB Serial/JTAG (usb_serial_jtag_ll.h). */
    RTCCNTL.usb_conf.sw_hw_usb_phy_sel = 1;
    RTCCNTL.usb_conf.sw_usb_phy_sel = 0;
    REG_WRITE(RTC_CNTL_OPTION1_REG, RTC_CNTL_FORCE_DOWNLOAD_BOOT);
    esp_restart();
}
