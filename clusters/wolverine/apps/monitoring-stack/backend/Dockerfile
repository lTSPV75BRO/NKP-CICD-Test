FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY . .
RUN go mod init monitoring-backend && go mod tidy && CGO_ENABLED=0 go build -o backend .

FROM alpine:latest
RUN apk --no-cache add curl kafkacat
WORKDIR /root/
COPY --from=builder /app/backend .
EXPOSE 8080
CMD ["./backend"]

