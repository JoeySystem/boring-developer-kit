#include "screen_glyph_store.h"
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"
#include "nvs_flash.h"
#ifdef ESP_PLATFORM
#include "esp_attr.h"
#else
#define EXT_RAM_BSS_ATTR
#endif

const screen_glyph_definition_t screen_glyph_catalog[SCREEN_GLYPH_COUNT] = {
    {"timer",11,12,true},{"settings",13,11,true},{"macos",13,5,true},
    {"windows_linux",14,5,true},{"back",7,7,true},{"confirm",10,7,true},
    {"power",7,7,true},{"warning",6,12,false},{"play",7,7,true},
    {"pause",7,7,true},{"cancel",7,7,true},{"error",9,9,false},
    {"config",7,7,true},{"lighting",13,12,true},{"haptic",13,11,true},
    {"standby",7,8,true},{"exit",7,7,true},{"normal_small",5,5,true},
    {"codex_small",5,5,true},{"ble_link",13,7,true},{"restart",13,13,true},
    {"system",11,10,true},{"normal_mode",13,13,true},{"codex_mode",15,15,true},
    {"claude_code_mode",15,15,true},{"usb",13,15,true},{"bluetooth",9,15,true},
    {"battery",13,9,true},
};
static nvs_handle_t s_nvs;
static SemaphoreHandle_t s_lock;
static atomic_bool s_ready;
static uint32_t s_epoch;
static EXT_RAM_BSS_ATTR screen_glyph_record_t s_records[SCREEN_GLYPH_COUNT];

int screen_glyph_find(const char *id) {
    if (id) for (unsigned i=0;i<SCREEN_GLYPH_COUNT;++i)
        if (!strcmp(id,screen_glyph_catalog[i].id)) return (int)i;
    return -1;
}
static size_t count(unsigned id) { return screen_glyph_catalog[id].width * screen_glyph_catalog[id].height; }
static void key(unsigned id,char out[5]) { snprintf(out,5,"g%02u",id); }
static size_t encode(unsigned id,const screen_glyph_record_t *r,uint8_t *out) {
    out[0]=1; out[1]=r->custom; out[2]=screen_glyph_catalog[id].width; out[3]=screen_glyph_catalog[id].height;
    for(unsigned j=0;j<4;++j) out[4+j]=(uint8_t)(r->revision>>(8*j));
    if(r->custom) memcpy(out+8,r->pixels,count(id));
    return 8+(r->custom?count(id):0);
}
esp_err_t screen_glyph_store_init(void) {
    atomic_store(&s_ready,false);
    if(!s_lock && !(s_lock=xSemaphoreCreateMutex())) return ESP_ERR_NO_MEM;
    xSemaphoreTake(s_lock,portMAX_DELAY);
    if(s_nvs) { nvs_close(s_nvs); s_nvs=0; }
    memset(s_records,0,sizeof(s_records)); ++s_epoch;
    esp_err_t err=nvs_flash_init_partition("prompt_nvs");
    if(err==ESP_OK) err=nvs_open_from_partition("prompt_nvs","screen_glyph",NVS_READWRITE,&s_nvs);
    if(err==ESP_OK) for(unsigned i=0;i<SCREEN_GLYPH_COUNT;++i) {
        char name[5]; key(i,name); uint8_t raw[8+SCREEN_GLYPH_MAX_BYTES]; size_t n=sizeof(raw);
        esp_err_t read=nvs_get_blob(s_nvs,name,raw,&n);
        if(read==ESP_ERR_NVS_NOT_FOUND) continue;
        if(read!=ESP_OK) { err=read; break; }
        if(n<8 || raw[0]!=1 || raw[1]>1 || raw[2]!=screen_glyph_catalog[i].width ||
           raw[3]!=screen_glyph_catalog[i].height || n!=8+(raw[1]?count(i):0)) { err=ESP_ERR_INVALID_RESPONSE; break; }
        screen_glyph_record_t *r=&s_records[i];
        for(unsigned j=0;j<4;++j) r->revision|=(uint32_t)raw[4+j]<<(8*j);
        r->custom=raw[1] && screen_glyph_catalog[i].editable;
        if(r->custom) memcpy(r->pixels,raw+8,count(i));
    }
    atomic_store(&s_ready,err==ESP_OK); xSemaphoreGive(s_lock); return err;
}
bool screen_glyph_store_ready(void) { return atomic_load(&s_ready); }
esp_err_t screen_glyph_store_get(unsigned id,screen_glyph_record_t *record) {
    if(id>=SCREEN_GLYPH_COUNT || !record) return ESP_ERR_INVALID_ARG;
    if(!screen_glyph_store_ready()) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_lock,portMAX_DELAY); *record=s_records[id]; xSemaphoreGive(s_lock); return ESP_OK;
}
static esp_err_t write_record(unsigned id,uint32_t base,const uint8_t *data,size_t length) {
    if(id>=SCREEN_GLYPH_COUNT || !screen_glyph_catalog[id].editable ||
        (data && length!=count(id))) return ESP_ERR_INVALID_ARG;
    if(!screen_glyph_store_ready()) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_lock,portMAX_DELAY);
    esp_err_t err=ESP_OK;
    if(base!=s_records[id].revision) err=ESP_ERR_INVALID_VERSION;
    else if(base==UINT32_MAX) err=ESP_ERR_INVALID_STATE;
    if(err==ESP_OK) {
        screen_glyph_record_t next={.revision=base+1,.custom=data!=NULL};
        if(data) memcpy(next.pixels,data,length);
        uint8_t raw[8+SCREEN_GLYPH_MAX_BYTES],readback[sizeof(raw)];
        size_t n=encode(id,&next,raw),read_length=sizeof(readback); char name[5]; key(id,name);
        err=nvs_set_blob(s_nvs,name,raw,n);
        if(err==ESP_OK) err=nvs_commit(s_nvs);
        if(err==ESP_OK) err=nvs_get_blob(s_nvs,name,readback,&read_length);
        if(err==ESP_OK && (read_length!=n || memcmp(raw,readback,n))) err=ESP_ERR_INVALID_RESPONSE;
        if(err==ESP_OK) { s_records[id]=next; ++s_epoch; }
        else {
            /* A blob is atomic, but a failed ACK must not leave an obsolete
             * RAM revision. Reload the actual selected record before retry. */
            xSemaphoreGive(s_lock); screen_glyph_store_init(); return err;
        }
    }
    xSemaphoreGive(s_lock); return err;
}
esp_err_t screen_glyph_store_set(unsigned id,uint32_t base,const uint8_t *data,size_t length) {
    if(!data) return ESP_ERR_INVALID_ARG;
    return write_record(id,base,data,length);
}
esp_err_t screen_glyph_store_reset(unsigned id,uint32_t base) { return write_record(id,base,NULL,0); }
bool screen_glyph_store_try_snapshot(uint32_t *epoch,screen_glyph_record_t *records) {
    if(!screen_glyph_store_ready() || !epoch || !records || xSemaphoreTake(s_lock,0)!=pdTRUE) return false;
    bool changed=*epoch!=s_epoch;
    if(changed) { memcpy(records,s_records,sizeof(s_records)); *epoch=s_epoch; }
    xSemaphoreGive(s_lock); return changed;
}
esp_err_t screen_glyph_store_erase_all(void) {
    if(!screen_glyph_store_ready()) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_lock,portMAX_DELAY); esp_err_t err=nvs_erase_all(s_nvs);
    if(err==ESP_OK) err=nvs_commit(s_nvs);
    xSemaphoreGive(s_lock);
    if(err==ESP_OK) err=screen_glyph_store_init();
    return err;
}
