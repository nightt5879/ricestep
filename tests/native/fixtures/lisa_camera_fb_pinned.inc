/* Unmodified test excerpt from c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35
 * arcs-sdk/drivers/lisa_camera/lisa_camera.c
 * Excerpt SHA-256: 63dac096f3fab75d62c749f2c55b80d7e3f4d74f65ebb8c0f12d656073ffc454
 * Source: https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/arcs-sdk/drivers/lisa_camera/lisa_camera.c
 * Public mirror provenance is described in docs/native-build.md.
 * SDK Apache-2.0 license is preserved beside this file: LICENSE.listenai-camera.
 * Only host tests include this; firmware compiles the actual SDK file. */
static int lisa_camera_fb_reinit(camera_priv_t *priv);

static int lisa_camera_fb_init(camera_priv_t *priv, uint32_t frame_size)
{
    uint8_t fb_count = priv->config.fb_count;
    uint32_t slot_size = (frame_size + 31U) & ~31U;
    if (fb_count == 0 || fb_count > MAX_BUF_NUM) {
        fb_count = 2;  /* 默认双缓冲 */
    }

    /* 创建队列 */
    priv->queue_free = lisa_queue_create(fb_count, NULL, sizeof(lisa_camera_fb_t *));
    priv->queue_filled = lisa_queue_create(fb_count, NULL, sizeof(lisa_camera_fb_t *));

    if (!priv->queue_free || !priv->queue_filled) {
        LOGE("Failed to create frame queues");
        return LISA_DEVICE_ERR_NO_MEM;
    }

    /* 分配帧缓冲区内存并初始化 */
    for (uint8_t i = 0; i < fb_count; i++) {
        /* 如果配置了外部 mem_pool，从外部内存地址分配 */
        if (priv->config.mem_pool != NULL) {
            /* 计算对齐后的偏移量 */
            uint32_t offset = i * slot_size;  /* 32字节对齐 */

            /* 检查是否超出内存池大小 */
            if (offset + frame_size > priv->config.mem_pool_size) {
                LOGE("Frame buffer %d exceeds mem_pool size (offset=%lu, size=%lu, pool_size=%lu)",
                     i, offset, frame_size, priv->config.mem_pool_size);
                return LISA_DEVICE_ERR_NO_MEM;
            }

            priv->fb_buf[i] = (uint8_t *)priv->config.mem_pool + offset;
            LOGD("Frame buffer %d allocated from external mem_pool: %p (offset=%lu)", i, priv->fb_buf[i], offset);
        } else {
            /* 使用系统默认内存分配 */
            priv->fb_buf[i] = (uint8_t *)lisa_mem_align_alloc(32, frame_size);
            if (!priv->fb_buf[i]) {
                LOGE("Failed to allocate frame buffer %d", i);
                /* 释放已分配的内存 */
                for (uint8_t j = 0; j < i; j++) {
                    lisa_mem_free(priv->fb_buf[j]);
                    priv->fb_buf[j] = NULL;
                }
                return LISA_DEVICE_ERR_NO_MEM;
            }
        }

        /* 初始化帧缓冲区结构 */
        priv->fb_list[i].buf = priv->fb_buf[i];
        priv->fb_list[i].len = frame_size;
        priv->fb_list[i].width = priv->frame_width;
        priv->fb_list[i].height = priv->frame_height;
        priv->fb_list[i].format = priv->pixel_format;
        priv->fb_list[i].timestamp = 0;

        /* 将帧缓冲区指针放入空闲队列 */
        lisa_camera_fb_t *fb_ptr = &priv->fb_list[i];
        lisa_queue_push(priv->queue_free, (void *)&fb_ptr, sizeof(fb_ptr), LISA_NO_WAIT);
    }

    priv->fb_count = fb_count;
    priv->fb_slot_size = slot_size;
    if (priv->config.mem_pool != NULL) {
        LOGI("Frame buffers initialized: count=%d, size=%lu, slot=%lu, external mem_pool=%p",
             fb_count, frame_size, priv->fb_slot_size, priv->config.mem_pool);
    } else {
        LOGI("Frame buffers initialized: count=%d, size=%lu (system memory)", fb_count, frame_size);
    }

    return LISA_DEVICE_OK;
}

static void lisa_camera_reset_frame_queues(camera_priv_t *priv)
{
    if (!priv || !priv->queue_free || !priv->queue_filled) {
        return;
    }

    lisa_camera_fb_t *fb;
    while (lisa_queue_pop(priv->queue_filled, (void *)&fb, sizeof(fb), LISA_NO_WAIT) == LISA_OK) {
    }
    while (lisa_queue_pop(priv->queue_free, (void *)&fb, sizeof(fb), LISA_NO_WAIT) == LISA_OK) {
    }

    for (uint8_t i = 0; i < priv->fb_count; i++) {
        lisa_camera_fb_t *fb_ptr = &priv->fb_list[i];
        lisa_queue_push(priv->queue_free, (void *)&fb_ptr, sizeof(fb_ptr), LISA_NO_WAIT);
    }

    LOGI("Frame buffer queues reset: %d buffers available", priv->fb_count);
}

static int lisa_camera_refresh_fb_metadata(camera_priv_t *priv, uint32_t frame_size)
{
    if (!priv) {
        return LISA_DEVICE_ERR_INVALID;
    }

    if (priv->fb_count == 0) {
        return lisa_camera_fb_init(priv, frame_size);
    }

    uint32_t slot_size = (frame_size + 31U) & ~31U;
    if (slot_size > priv->fb_slot_size) {
        if (priv->config.mem_pool != NULL) {
            uint32_t required = slot_size * priv->fb_count;
            if (required > priv->config.mem_pool_size) {
                LOGE("Frame buffers need %lu bytes, mem_pool only %lu", required, priv->config.mem_pool_size);
                return LISA_DEVICE_ERR_NO_MEM;
            }
        } else {
            return lisa_camera_fb_reinit(priv);
        }
        priv->fb_slot_size = slot_size;
    }

    for (uint8_t i = 0; i < priv->fb_count; i++) {
        if (priv->config.mem_pool != NULL) {
            priv->fb_buf[i] = (uint8_t *)priv->config.mem_pool + (i * priv->fb_slot_size);
        }
        priv->fb_list[i].buf = priv->fb_buf[i];
        priv->fb_list[i].len = frame_size;
        priv->fb_list[i].width = priv->frame_width;
        priv->fb_list[i].height = priv->frame_height;
        priv->fb_list[i].format = priv->pixel_format;
        priv->fb_list[i].timestamp = 0;
    }
    lisa_camera_reset_frame_queues(priv);
    LOGI("Frame buffers refreshed: count=%d, frame=%lu, slot=%lu", priv->fb_count, frame_size, priv->fb_slot_size);
    return LISA_DEVICE_OK;
}

/**
 * @brief 重新初始化帧缓冲区
 */
static int lisa_camera_fb_reinit(camera_priv_t *priv)
{
    uint32_t frame_size = priv->frame_width * priv->frame_height * 2;  /* 默认 RGB565/YUV422 */
    uint32_t slot_size;
    if (priv->pixel_format == LISA_CAMERA_PIXFMT_GRAY) {
        frame_size = priv->frame_width * priv->frame_height;
    }
    slot_size = (frame_size + 31U) & ~31U;

    /* 仅重新分配缓冲区内存，不销毁队列 */
    for (uint8_t i = 0; i < priv->fb_count; i++) {
        if (priv->fb_buf[i]) {
            /* 如果使用了外部 mem_pool，不需要释放旧内存（由外部管理） */
            if (priv->config.mem_pool == NULL) {
                lisa_mem_free(priv->fb_buf[i]);
            }
        }

        /* 分配新内存 */
        if (priv->config.mem_pool != NULL) {
            /* 从外部内存地址分配 */
            uint32_t offset = i * slot_size;  /* 32字节对齐 */

            /* 检查是否超出内存池大小 */
            if (offset + frame_size > priv->config.mem_pool_size) {
                LOGE("Frame buffer %d exceeds mem_pool size during reinit", i);
                return LISA_DEVICE_ERR_NO_MEM;
            }

            priv->fb_buf[i] = (uint8_t *)priv->config.mem_pool + offset;
        } else {
            /* 使用系统内存分配 */
            priv->fb_buf[i] = (uint8_t *)lisa_mem_align_alloc(32, frame_size);
            if (!priv->fb_buf[i]) {
                LOGE("Failed to re-allocate frame buffer %d", i);
                /* 释放已分配的内存 */
                for (uint8_t j = 0; j < i; j++) {
                    lisa_mem_free(priv->fb_buf[j]);
                    priv->fb_buf[j] = NULL;
                }
                return LISA_DEVICE_ERR_NO_MEM;
            }
        }

        priv->fb_list[i].buf = priv->fb_buf[i];
        priv->fb_list[i].len = frame_size;
        priv->fb_list[i].width = priv->frame_width;
        priv->fb_list[i].height = priv->frame_height;
    }

    priv->fb_slot_size = slot_size;
    lisa_camera_reset_frame_queues(priv);
    LOGI("Frame buffers re-initialized: count=%d, size=%lu", priv->fb_count, frame_size);

    return LISA_DEVICE_OK;
}

static void lisa_camera_fb_deinit(camera_priv_t *priv)
{
    /* 删除队列 */
    if (priv->queue_free) {
        lisa_queue_delete(priv->queue_free);
        priv->queue_free = NULL;
    }
    if (priv->queue_filled) {
        lisa_queue_delete(priv->queue_filled);
        priv->queue_filled = NULL;
    }

    /* 释放帧缓冲区内存 */
    for (uint8_t i = 0; i < priv->fb_count; i++) {
        if (priv->fb_buf[i]) {
            /* 如果使用了外部 mem_pool，不需要释放（由外部管理） */
            if (priv->config.mem_pool == NULL) {
                lisa_mem_free(priv->fb_buf[i]);
            }
            priv->fb_buf[i] = NULL;
        }
    }
    priv->fb_count = 0;
}

