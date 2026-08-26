package com.alertmanagement.backend.security;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.OffsetDateTime;
import java.util.UUID;

record CsrfView(String token, @JsonProperty("header_name") String headerName,
        @JsonProperty("parameter_name") String parameterName) {
}

record LoginRequest(String username, String password) {
}

record CurrentUserView(
        @JsonProperty("user_id") UUID userId,
        String username,
        @JsonProperty("display_name") String displayName,
        @JsonProperty("global_role") String globalRole,
        @JsonProperty("must_change_password") boolean mustChangePassword) {
    static CurrentUserView from(Actor actor) {
        return new CurrentUserView(actor.userId(), actor.username(), actor.displayName(),
                actor.globalRole(), actor.mustChangePassword());
    }
}

record PasswordChangeRequest(
        @JsonProperty("current_password") String currentPassword,
        @JsonProperty("new_password") String newPassword) {
}

record UserView(
        @JsonProperty("user_id") UUID userId,
        String username,
        @JsonProperty("display_name") String displayName,
        @JsonProperty("global_role") String globalRole,
        String status,
        @JsonProperty("must_change_password") boolean mustChangePassword,
        @JsonProperty("locked_until") OffsetDateTime lockedUntil,
        @JsonProperty("created_at") OffsetDateTime createdAt) {
}

record UserCreateRequest(
        String username,
        @JsonProperty("display_name") String displayName,
        String password,
        @JsonProperty("global_role") String globalRole) {
}

record UserPatchRequest(
        @JsonProperty("display_name") String displayName,
        String status,
        @JsonProperty("global_role") String globalRole) {
}

record PasswordResetRequest(@JsonProperty("new_password") String newPassword) {
}

record ProjectMemberView(
        @JsonProperty("user_id") UUID userId,
        String username,
        @JsonProperty("display_name") String displayName,
        String status,
        @JsonProperty("project_role") String projectRole) {
}

record ProjectMemberRequest(@JsonProperty("project_role") String projectRole) {
}
