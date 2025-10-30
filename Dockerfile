FROM public.ecr.aws/lambda/python:3.11-arm64

# Install Python dependencies into Lambda task root
COPY lambdas/clean_transform/requirements.txt .
RUN pip3.11 install -r requirements.txt --target "${LAMBDA_TASK_ROOT}" \
	&& rm -f requirements.txt

# Copy function code
COPY lambdas/clean_transform ${LAMBDA_TASK_ROOT}/lambdas/clean_transform

# Set the handler (module.function)
CMD ["lambdas.clean_transform.handler.lambda_handler"]
