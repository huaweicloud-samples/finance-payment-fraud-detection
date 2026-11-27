#
# Build stage
#
FROM maven:3.9-eclipse-temurin-11 AS build
ENV WORKDIR=/usr/app
RUN mkdir -p $WORKDIR
WORKDIR $WORKDIR
ADD . $WORKDIR
RUN mvn -f $WORKDIR/pom.xml clean compile assembly:single

#
# Package stage
#
FROM eclipse-temurin:11-jre
ARG JAR_FILE=/usr/app/target/*.jar
COPY --from=build $JAR_FILE /app/runner.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/runner.jar"]
