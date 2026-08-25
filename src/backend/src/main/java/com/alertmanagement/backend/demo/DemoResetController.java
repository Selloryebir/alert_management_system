package com.alertmanagement.backend.demo;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/demo")
class DemoResetController {

    private final DemoResetService resetService;

    DemoResetController(DemoResetService resetService) {
        this.resetService = resetService;
    }

    @PostMapping("/reset")
    DemoResetView reset(@RequestBody(required = false) DemoResetRequest request) {
        return resetService.reset(request);
    }
}
