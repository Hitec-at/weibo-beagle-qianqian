const test = require("node:test");
const assert = require("node:assert/strict");
const { createClient, normalisePath } = require("../utils/api");

function fakeWx(handler) {
  return {
    cloud: {
      callContainer(options) {
        handler(options);
      }
    }
  };
}

test("normalisePath keeps the API prefix exactly once", () => {
  assert.equal(normalisePath("/api/v1", "/posts"), "/api/v1/posts");
  assert.equal(normalisePath("/api/v1", "/api/v1/posts"), "/api/v1/posts");
});

test("requestData calls the cloud container with service routing", async () => {
  let captured;
  const wxApi = fakeWx(options => {
    captured = options;
    options.success({
      statusCode: 200,
      data: { ok: true, data: { items: [1] } }
    });
  });
  const client = createClient(wxApi, {
    cloudEnv: "prod-1",
    serviceName: "weibo-api",
    apiPrefix: "/api/v1",
    requestTimeout: 15000
  });

  const data = await client.requestData("/posts", { page: 2 });

  assert.deepEqual(data, { items: [1] });
  assert.equal(captured.path, "/api/v1/posts");
  assert.equal(captured.method, "GET");
  assert.equal(captured.header["X-WX-SERVICE"], "weibo-api");
  assert.equal(captured.config.env, "prod-1");
  assert.equal(captured.timeout, 15000);
  assert.equal(captured.responseType, undefined);
});

test("requestData reports API errors", async () => {
  const wxApi = fakeWx(options => {
    options.success({
      statusCode: 404,
      data: { detail: "微博不存在" }
    });
  });
  const client = createClient(wxApi, {
    cloudEnv: "prod-1",
    serviceName: "weibo-api"
  });

  await assert.rejects(
    client.requestData("/posts/missing"),
    /微博不存在/
  );
});

test("missing environment fails before a network call", async () => {
  let called = false;
  const wxApi = fakeWx(() => {
    called = true;
  });
  const client = createClient(wxApi, {
    cloudEnv: "",
    serviceName: ""
  });

  await assert.rejects(client.requestData("/bootstrap"), /配置云托管/);
  assert.equal(called, false);
});

test("array buffer requests opt into the binary response mode", async () => {
  let captured;
  const wxApi = fakeWx(options => {
    captured = options;
    options.success({ statusCode: 200, data: new ArrayBuffer(4) });
  });
  const client = createClient(wxApi, {
    cloudEnv: "prod-1",
    serviceName: "weibo-api"
  });

  const data = await client.requestArrayBuffer("/media/image", { u: "https://x" });

  assert.equal(captured.responseType, "arraybuffer");
  assert.equal(data.byteLength, 4);
});
