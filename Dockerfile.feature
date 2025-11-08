FROM public.ecr.aws/lambda/python:3.11-arm64

# Install Python dependencies into Lambda task root
COPY lambdas/feature_build/requirements.txt .
RUN pip3.11 install --upgrade pip \
	&& pip3.11 install -r requirements.txt --target "${LAMBDA_TASK_ROOT}" \
	&& rm -f requirements.txt

# Copy function code
COPY lambdas/feature_build ${LAMBDA_TASK_ROOT}/lambdas/feature_build

# Set the handler (module.function)
CMD ["lambdas.feature_build.handler.lambda_handler"]

