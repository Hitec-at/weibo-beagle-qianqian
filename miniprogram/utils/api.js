function normalisePath(prefix, path) {
  const value = path || "/";
  if (value.startsWith(prefix + "/")) return value;
  return prefix + (value.startsWith("/") ? value : "/" + value);
}

function createClient(wxApi, settings) {
  const config = settings || {};
  const prefix = config.apiPrefix || "/api/v1";
  const timeout = config.requestTimeout || 15000;

  function callContainer(options) {
    if (!wxApi || !wxApi.cloud || !wxApi.cloud.callContainer) {
      return Promise.reject(new Error("当前基础库不支持微信云托管调用"));
    }
    if (!config.cloudEnv || !config.serviceName) {
      return Promise.reject(new Error("请先配置云托管环境 ID 和服务名称"));
    }
    return new Promise((resolve, reject) => {
      const request = {
        config: { env: config.cloudEnv },
        path: normalisePath(prefix, options.path),
        method: options.method || "GET",
        data: options.data || {},
        timeout: Math.min(options.timeout || timeout, 15000),
        header: {
          "X-WX-SERVICE": config.serviceName
        },
        success: resolve,
        fail(error) {
          reject(new Error(error && error.errMsg ? error.errMsg : "云托管调用失败"));
        }
      };
      if (options.responseType) request.responseType = options.responseType;
      wxApi.cloud.callContainer(request);
    });
  }

  async function requestData(path, data, options) {
    const response = await callContainer({
      path,
      data: data || {},
      method: (options && options.method) || "GET"
    });
    const statusCode = Number(response.statusCode || 200);
    const body = response.data;
    if (statusCode < 200 || statusCode >= 300) {
      const message = body && body.detail ? body.detail : "接口返回异常";
      throw new Error(message + "（" + statusCode + "）");
    }
    if (!body || body.ok !== true || body.data === undefined) {
      throw new Error("接口响应格式错误");
    }
    return body.data;
  }

  async function requestArrayBuffer(path, data) {
    const response = await callContainer({
      path,
      data: data || {},
      responseType: "arraybuffer"
    });
    if (Number(response.statusCode || 200) !== 200) {
      throw new Error("媒体加载失败");
    }
    return response.data;
  }

  return {
    requestData,
    requestArrayBuffer
  };
}

module.exports = {
  createClient,
  normalisePath
};
