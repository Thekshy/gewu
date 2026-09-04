package gateway

import (
	"net/http"

	"github.com/gin-gonic/gin"

	orchestratorv1 "gewu/pkg/gen/gewu/orchestrator/v1"
)

// /admin/* 配置管理命名空间（新增，不触碰 /api/* 契约）：转发 orchestrator。
//
//	GET  /admin/config → 当前生效配置（含 is_default）
//	PUT  /admin/config → 更新（空字段保持不变）
func (s *Server) adminConfig(c *gin.Context) {
	if c.Request.Method == http.MethodGet {
		resp, err := s.config.GetConfig(c.Request.Context(), &orchestratorv1.GetConfigRequest{})
		if err != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{"detail": "orchestrator 不可达"})
			return
		}
		c.JSON(http.StatusOK, gin.H{
			"is_default": resp.GetIsDefault(),
			"config": gin.H{
				"router_system":       resp.GetConfig().GetRouterSystem(),
				"slot_extract_system": resp.GetConfig().GetSlotExtractSystem(),
				"planner_system":      resp.GetConfig().GetPlannerSystem(),
				"answer_system":       resp.GetConfig().GetAnswerSystem(),
			},
		})
		return
	}
	if c.Request.Method == http.MethodPut || c.Request.Method == http.MethodPost {
		var body struct {
			RouterSystem   *string `json:"router_system"`
			SlotExtractSys *string `json:"slot_extract_system"`
			PlannerSystem  *string `json:"planner_system"`
			AnswerSystem   *string `json:"answer_system"`
		}
		if err := c.ShouldBindJSON(&body); err != nil {
			unprocessable(c, "请求体不是合法 JSON")
			return
		}
		req := &orchestratorv1.SetConfigRequest{Config: &orchestratorv1.AgentConfig{}}
		if body.RouterSystem != nil {
			req.Config.RouterSystem = *body.RouterSystem
		}
		if body.SlotExtractSys != nil {
			req.Config.SlotExtractSystem = *body.SlotExtractSys
		}
		if body.PlannerSystem != nil {
			req.Config.PlannerSystem = *body.PlannerSystem
		}
		if body.AnswerSystem != nil {
			req.Config.AnswerSystem = *body.AnswerSystem
		}
		resp, err := s.config.SetConfig(c.Request.Context(), req)
		if err != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{"detail": "orchestrator 不可达"})
			return
		}
		c.JSON(http.StatusOK, gin.H{"config": gin.H{
			"router_system":       resp.GetConfig().GetRouterSystem(),
			"slot_extract_system": resp.GetConfig().GetSlotExtractSystem(),
			"planner_system":      resp.GetConfig().GetPlannerSystem(),
			"answer_system":       resp.GetConfig().GetAnswerSystem(),
		}})
		return
	}
	c.JSON(http.StatusMethodNotAllowed, gin.H{"detail": "仅支持 GET/PUT"})
}
