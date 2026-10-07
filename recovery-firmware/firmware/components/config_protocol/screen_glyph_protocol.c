#include "screen_glyph_protocol.h"
#include "screen_glyph_store.h"
#include "mist_glyph_assets.h"
#include "protocol_contract.h"
#include "mbedtls/base64.h"
#include <math.h>
#include <string.h>

static void send(screen_icon_response_fn respond,uint8_t type,uint32_t id,cJSON *root) {
    char *json=cJSON_PrintUnformatted(root);
    if(json) respond(type,id,json);
    cJSON_free(json); cJSON_Delete(root);
}
static void error(screen_icon_response_fn respond,uint32_t id,const char *command,
                  uint16_t code,const char *name,int glyph) {
    cJSON *root=cJSON_CreateObject(); cJSON_AddStringToObject(root,"command",command);
    cJSON *err=cJSON_AddObjectToObject(root,"error"); cJSON_AddNumberToObject(err,"code",code);
    cJSON_AddStringToObject(err,"name",name); cJSON_AddStringToObject(err,"message","screen glyph operation failed");
    cJSON *details=cJSON_AddObjectToObject(err,"details");
    if(code==WMP_ERROR_GENERATION_CONFLICT && glyph>=0) {
        screen_glyph_record_t record;
        if(screen_glyph_store_get(glyph,&record)==ESP_OK) cJSON_AddNumberToObject(details,"current_revision",record.revision);
    }
    send(respond,WMP_MSG_NACK,id,root);
}
void screen_glyph_protocol_add_capability(cJSON *result,bool supported) {
    cJSON_AddBoolToObject(cJSON_GetObjectItemCaseSensitive(result,"features"),"custom_glyph_icons",supported);
    if(!supported) return;
    cJSON *glyphs=cJSON_AddObjectToObject(result,"screen_glyphs");
    cJSON_AddNumberToObject(glyphs,"version",1); cJSON_AddStringToObject(glyphs,"format","alpha8");
    cJSON_AddNumberToObject(glyphs,"count",SCREEN_GLYPH_COUNT);
}
static cJSON *root_for(const char *command) {
    cJSON *root=cJSON_CreateObject(); cJSON_AddStringToObject(root,"command",command);
    cJSON_AddObjectToObject(root,"result"); return root;
}
static void metadata(screen_icon_response_fn respond,uint32_t id,const char *command,unsigned index) {
    screen_glyph_record_t record;
    if(screen_glyph_store_get(index,&record)!=ESP_OK) {
        error(respond,id,command,WMP_ERROR_STORAGE_FAILURE,"STORAGE_FAILURE",index); return;
    }
    const screen_glyph_definition_t *def=&screen_glyph_catalog[index];
    const mist_glyph_icon_t *builtin=mist_glyph_icon_get(index);
    if(!record.custom) for(unsigned y=0;y<def->height;++y) for(unsigned x=0;x<def->width;++x) {
        const unsigned offset=y*def->width+x;
        record.pixels[offset]=(builtin->rows[y] & (1u<<(def->width-1u-x)))
            ? (builtin->levels?builtin->levels[offset]:255) : 0;
    }
    unsigned char encoded[301]; size_t length=0;
    mbedtls_base64_encode(encoded,sizeof(encoded),&length,record.pixels,def->width*def->height);
    encoded[length]=0;
    cJSON *root=root_for(command),*r=cJSON_GetObjectItemCaseSensitive(root,"result");
    cJSON_AddStringToObject(r,"id",def->id); cJSON_AddNumberToObject(r,"revision",record.revision);
    cJSON_AddStringToObject(r,"source",record.custom?"custom":"default");
    cJSON_AddNumberToObject(r,"width",def->width); cJSON_AddNumberToObject(r,"height",def->height);
    cJSON_AddStringToObject(r,"format","alpha8"); cJSON_AddStringToObject(r,"data",(char *)encoded);
    send(respond,WMP_MSG_ACK,id,root);
}
void screen_glyph_protocol_handle(uint8_t type,uint32_t id,const cJSON *request,
    bool supported,bool session_ready,bool busy,screen_icon_response_fn respond) {
    static const char *commands[]={"SCREEN_GLYPH_LIST","SCREEN_GLYPH_GET","SCREEN_GLYPH_SET","SCREEN_GLYPH_RESET"};
    if(type<WMP_MSG_SCREEN_GLYPH_LIST || type>WMP_MSG_SCREEN_GLYPH_RESET || !respond) return;
    const char *command=commands[type-WMP_MSG_SCREEN_GLYPH_LIST];
    int index=-1; const int count=cJSON_GetArraySize(request);
    if(!supported) { error(respond,id,command,WMP_ERROR_NOT_FOUND,"NOT_FOUND",index); return; }
    if(!session_ready) goto is_busy;
    if(type==WMP_MSG_SCREEN_GLYPH_LIST) {
        if(count) goto invalid;
        cJSON *root=root_for(command),*r=cJSON_GetObjectItemCaseSensitive(root,"result");
        cJSON_AddNumberToObject(r,"version",1); cJSON_AddStringToObject(r,"format","alpha8");
        cJSON *icons=cJSON_AddArrayToObject(r,"icons");
        for(unsigned i=0;i<SCREEN_GLYPH_COUNT;++i) {
            const screen_glyph_definition_t *def=&screen_glyph_catalog[i]; cJSON *item=cJSON_CreateObject();
            cJSON_AddStringToObject(item,"id",def->id); cJSON_AddNumberToObject(item,"resource_id",i);
            cJSON_AddNumberToObject(item,"width",def->width); cJSON_AddNumberToObject(item,"height",def->height);
            cJSON_AddBoolToObject(item,"editable",def->editable); cJSON_AddItemToArray(icons,item);
        }
        send(respond,WMP_MSG_ACK,id,root); return;
    }
    const cJSON *name=cJSON_GetObjectItemCaseSensitive(request,"id");
    if(!cJSON_IsString(name) || (index=screen_glyph_find(name->valuestring))<0) goto invalid;
    if(type==WMP_MSG_SCREEN_GLYPH_GET) {
        if(count!=1) goto invalid;
        metadata(respond,id,command,index); return;
    }
    if(!screen_glyph_catalog[index].editable) goto invalid;
    if(busy) goto is_busy;
    const cJSON *base=cJSON_GetObjectItemCaseSensitive(request,"base_revision");
    if(!cJSON_IsNumber(base) || !isfinite(base->valuedouble) || base->valuedouble<0 ||
       base->valuedouble>UINT32_MAX || floor(base->valuedouble)!=base->valuedouble) goto invalid;
    esp_err_t result;
    if(type==WMP_MSG_SCREEN_GLYPH_RESET) {
        if(count!=2) goto invalid;
        result=screen_glyph_store_reset(index,(uint32_t)base->valuedouble);
    } else {
        const cJSON *data=cJSON_GetObjectItemCaseSensitive(request,"data");
        if(count!=3 || !cJSON_IsString(data) || !data->valuestring) goto invalid;
        const size_t encoded_length=strlen(data->valuestring);
        uint8_t alpha[SCREEN_GLYPH_MAX_BYTES]; unsigned char canonical[301]; size_t decoded=0,n=0;
        if(encoded_length>300 || mbedtls_base64_decode(alpha,sizeof(alpha),&decoded,(unsigned char *)data->valuestring,encoded_length) ||
           decoded!=screen_glyph_catalog[index].width*screen_glyph_catalog[index].height ||
           mbedtls_base64_encode(canonical,sizeof(canonical),&n,alpha,decoded) || n!=encoded_length ||
           memcmp(canonical,data->valuestring,n)) goto invalid;
        result=screen_glyph_store_set(index,(uint32_t)base->valuedouble,alpha,decoded);
    }
    if(result!=ESP_OK) {
        error(respond,id,command,result==ESP_ERR_INVALID_VERSION?WMP_ERROR_GENERATION_CONFLICT:WMP_ERROR_STORAGE_FAILURE,
            result==ESP_ERR_INVALID_VERSION?"GENERATION_CONFLICT":"STORAGE_FAILURE",index); return;
    }
    metadata(respond,id,command,index); return;
invalid:
    error(respond,id,command,WMP_ERROR_VALIDATION_FAILED,"VALIDATION_FAILED",index); return;
is_busy:
    error(respond,id,command,WMP_ERROR_BUSY,"BUSY",index);
}
