import { OpenAPIRoute, contentJson, fromHono } from 'chanfana';
import { z } from 'zod';

const nullableStringSchema = z.string().nullable();
const unknownObjectSchema = z.object({}).catchall(z.any());
const emptyObjectSchema = z.object({});
const visibilitySchema = z.enum(['public', 'private']);
const roleSchema = z.enum(['admin', 'user', 'readonly']);

const errorDetailSchema = z.object({
  code: z.number().int().optional(),
  message: z.string(),
  path: z.array(z.string()).optional(),
});

const errorResponseSchema = z.object({
  message: z.string(),
  errors: z.array(errorDetailSchema).optional(),
}).catchall(z.any());

const authProvidersResponseSchema = z.object({
  google: z.boolean(),
});

const siteSettingsResponseSchema = z.object({
  allowUserRegistration: z.boolean(),
  updatedAt: z.string(),
  updatedByUserId: nullableStringSchema,
});

const siteSettingsUpdateRequestSchema = z.object({
  allowUserRegistration: z.boolean(),
});

const managementPurgeAllAssetsRequestSchema = z.object({
  confirmationText: z.string(),
});

const managementPurgeAllAssetsResponseSchema = z.object({
  deletedAssetSubscriptions: z.number().int(),
  deletedAssetVersions: z.number().int(),
  deletedVariantDetails: z.number().int(),
  deletedAssets: z.number().int(),
});

const apiUserResponseSchema = z.object({
  userId: z.string(),
  username: z.string(),
  displayName: z.string(),
  email: z.string(),
  emailVerified: z.boolean(),
  isAdmin: z.boolean(),
  role: roleSchema,
  canUpload: z.boolean(),
});

const managementUserResponseSchema = apiUserResponseSchema.extend({
  assetCount: z.number().int(),
  createdAt: z.string(),
  updatedAt: z.string(),
});

const managementUserPageResponseSchema = z.object({
  entries: z.array(managementUserResponseSchema),
  nextCursor: nullableStringSchema,
});

const assetSubscriptionSchema = z.object({
  subscribedAt: z.string(),
  lastSeenRevision: nullableStringSchema,
});

const typedAssetBaseSchema = z.object({
  ownerUserId: z.string(),
  ownerDisplayName: nullableStringSchema,
  visibility: visibilitySchema,
  revision: z.string(),
  latestRevision: z.string(),
  versionNumber: z.number().int(),
  latestVersionNumber: z.number().int(),
  changeSummary: nullableStringSchema,
  name: z.string(),
  description: z.string(),
  tags: z.array(z.string()),
  createdAt: z.string(),
  updatedAt: z.string(),
  isOwner: z.boolean(),
  subscribed: z.boolean(),
  editable: z.boolean(),
  subscription: assetSubscriptionSchema.nullable(),
});

const variantRecordSchema = z.object({
  variantId: z.string(),
  kind: z.string(),
  baseNodeType: z.string(),
  serviceClass: z.string(),
  operatorClass: nullableStringSchema,
  name: z.string(),
  description: z.string(),
  tags: z.array(z.string()),
  spec: unknownObjectSchema,
  createdAt: z.string(),
  updatedAt: z.string(),
});

const variantRecordRequestSchema = z.object({
  variantId: z.string(),
  kind: z.string(),
  baseNodeType: z.string(),
  serviceClass: z.string(),
  operatorClass: nullableStringSchema.optional(),
  name: z.string(),
  description: z.string().optional(),
  tags: z.array(z.string()).optional(),
  spec: unknownObjectSchema,
  createdAt: z.string().optional(),
  updatedAt: z.string().optional(),
});

const componentRecordSchema = z.object({
  componentId: z.string(),
  name: z.string(),
  description: z.string(),
  tags: z.array(z.string()),
  schemaVersion: z.string(),
  content: unknownObjectSchema,
  createdAt: z.string(),
  updatedAt: z.string(),
});

const componentRecordRequestSchema = z.object({
  componentId: z.string(),
  name: z.string(),
  description: z.string().optional(),
  tags: z.array(z.string()).optional(),
  schemaVersion: z.string(),
  content: unknownObjectSchema,
  createdAt: z.string().optional(),
  updatedAt: z.string().optional(),
});

const variantSummarySchema = typedAssetBaseSchema.extend({
  assetType: z.literal('variant'),
  variantId: z.string(),
  variantKind: z.string(),
  baseNodeType: z.string(),
  serviceClass: z.string(),
  operatorClass: nullableStringSchema,
  hasContent: z.boolean(),
});

const componentSummarySchema = typedAssetBaseSchema.extend({
  assetType: z.literal('component'),
  componentId: z.string(),
  schemaVersion: z.string(),
  hasContent: z.boolean(),
});

const variantDetailSchema = variantSummarySchema.extend({
  versionCreatedAt: z.string(),
  createdByUserId: z.string(),
});

const componentDetailSchema = componentSummarySchema.extend({
  versionCreatedAt: z.string(),
  createdByUserId: z.string(),
});

const variantContentResponseSchema = z.object({
  variantId: z.string(),
  assetType: z.literal('variant'),
  versionNumber: z.number().int(),
  revision: z.string(),
  record: variantRecordSchema,
});

const componentContentResponseSchema = z.object({
  componentId: z.string(),
  assetType: z.literal('component'),
  versionNumber: z.number().int(),
  revision: z.string(),
  record: componentRecordSchema,
});

const variantPageResponseSchema = z.object({
  entries: z.array(variantSummarySchema),
  nextCursor: nullableStringSchema,
});

const componentPageResponseSchema = z.object({
  entries: z.array(componentSummarySchema),
  nextCursor: nullableStringSchema,
});

const assetVersionBaseSchema = z.object({
  assetType: z.enum(['component', 'variant']),
  versionNumber: z.number().int(),
  revision: z.string(),
  createdAt: z.string(),
  createdByUserId: z.string(),
  changeSummary: nullableStringSchema,
});

const variantVersionSummarySchema = assetVersionBaseSchema.extend({
  assetType: z.literal('variant'),
  variantId: z.string(),
});

const componentVersionSummarySchema = assetVersionBaseSchema.extend({
  assetType: z.literal('component'),
  componentId: z.string(),
});

const variantVersionPageResponseSchema = z.object({
  versions: z.array(variantVersionSummarySchema),
  nextCursor: nullableStringSchema,
});

const componentVersionPageResponseSchema = z.object({
  versions: z.array(componentVersionSummarySchema),
  nextCursor: nullableStringSchema,
});

const adminAssetBaseSchema = z.object({
  assetId: z.string(),
  assetType: z.enum(['component', 'variant']),
  ownerUserId: z.string(),
  ownerDisplayName: nullableStringSchema,
  visibility: visibilitySchema,
  revision: z.string(),
  latestRevision: z.string(),
  versionNumber: z.number().int(),
  latestVersionNumber: z.number().int(),
  changeSummary: nullableStringSchema,
  name: z.string(),
  description: z.string(),
  tags: z.array(z.string()),
  createdAt: z.string(),
  updatedAt: z.string(),
  deletedAt: nullableStringSchema,
});

const adminVariantSummarySchema = adminAssetBaseSchema.extend({
  assetType: z.literal('variant'),
  variantKind: z.string(),
  baseNodeType: z.string(),
  serviceClass: z.string(),
  operatorClass: nullableStringSchema,
});

const adminComponentSummarySchema = adminAssetBaseSchema.extend({
  assetType: z.literal('component'),
  schemaVersion: nullableStringSchema,
});

const adminVariantDetailSchema = adminVariantSummarySchema.extend({
  record: variantRecordSchema,
});

const adminComponentDetailSchema = adminComponentSummarySchema.extend({
  record: componentRecordSchema,
});

const managementAssetPageResponseSchema = z.object({
  entries: z.array(z.union([adminComponentSummarySchema, adminVariantSummarySchema])),
  nextCursor: nullableStringSchema,
});

const managementAssetDetailResponseSchema = z.union([adminComponentDetailSchema, adminVariantDetailSchema]);

const componentListQuerySchema = z.object({
  q: z.string().optional(),
  visibility: visibilitySchema.optional(),
  owner: z.enum(['public', 'me', 'subscribed']).optional(),
  cursor: z.string().optional(),
});

const variantListQuerySchema = componentListQuerySchema.extend({
  kind: z.string().optional(),
  baseNodeType: z.string().optional(),
});

const versionListQuerySchema = z.object({
  cursor: z.string().optional(),
});

const userListQuerySchema = z.object({
  q: z.string().optional(),
  cursor: z.string().optional(),
});

const managementComponentListQuerySchema = z.object({
  ownerUserId: z.string().optional(),
  q: z.string().optional(),
  includeDeleted: z.enum(['true', 'false']).optional(),
  cursor: z.string().optional(),
});

const managementVariantListQuerySchema = managementComponentListQuerySchema.extend({
  kind: z.string().optional(),
  baseNodeType: z.string().optional(),
});

const managementAssetDetailQuerySchema = z.object({
  includeDeleted: z.enum(['true', 'false']).optional(),
});

const variantCreateRequestSchema = z.object({
  record: variantRecordRequestSchema,
  visibility: visibilitySchema.optional(),
  changeSummary: z.string().optional(),
});

const variantUpdateRequestSchema = variantCreateRequestSchema.extend({
  revision: z.string().optional(),
});

const componentCreateRequestSchema = z.object({
  record: componentRecordRequestSchema,
  visibility: visibilitySchema.optional(),
  changeSummary: z.string().optional(),
});

const componentUpdateRequestSchema = componentCreateRequestSchema.extend({
  revision: z.string().optional(),
});

const visibilityUpdateRequestSchema = z.object({
  visibility: visibilitySchema,
  revision: z.string().optional(),
});

const assetMetaUpdateRequestSchema = z.object({
  name: z.string(),
  description: z.string().optional(),
  tags: z.array(z.string()).optional(),
});

const variantForkRequestSchema = z.object({
  variantId: z.string().optional(),
  name: z.string().optional(),
  visibility: visibilitySchema.optional(),
  changeSummary: z.string().optional(),
});

const componentForkRequestSchema = z.object({
  componentId: z.string().optional(),
  name: z.string().optional(),
  visibility: visibilitySchema.optional(),
  changeSummary: z.string().optional(),
});

const managementUserCreateRequestSchema = z.object({
  username: z.string(),
  email: z.string(),
  password: z.string(),
  displayName: z.string().optional(),
  role: roleSchema.optional(),
  isAdmin: z.boolean().optional(),
  canUpload: z.boolean().optional(),
});

const managementUserUpdateRequestSchema = z.object({
  username: z.string().optional(),
  displayName: z.string().optional(),
  password: z.string().optional(),
  role: roleSchema.optional(),
  isAdmin: z.boolean().optional(),
  canUpload: z.boolean().optional(),
});

const managementAssetUpdateRequestSchema = z.object({
  restore: z.boolean().optional(),
  visibility: visibilitySchema.optional(),
});

function createJsonEndpoint({ schema, handler }) {
  return class extends OpenAPIRoute {
    schema = schema;

    async handle(c) {
      return handler(c);
    }
  };
}

function jsonSuccessResponse(schema, description) {
  return {
    description,
    ...contentJson(schema),
  };
}

function jsonRequestBody(schema, description) {
  return {
    description,
    required: true,
    ...contentJson(schema),
  };
}

function jsonErrorResponse(description) {
  return {
    description,
    ...contentJson(errorResponseSchema),
  };
}

function withCommonErrorResponses(responses) {
  return {
    400: jsonErrorResponse('Bad request'),
    401: jsonErrorResponse('Authentication required'),
    403: jsonErrorResponse('Forbidden'),
    404: jsonErrorResponse('Not found'),
    409: jsonErrorResponse('Conflict'),
    500: jsonErrorResponse('Internal error'),
    ...responses,
  };
}

function registerRoute(openapi, method, path, schema, handler) {
  openapi[method](path, createJsonEndpoint({ schema, handler }));
}

export function registerOpenApiRoutes(app, handlers) {
  const openapi = fromHono(app, {
    docs_url: '/docs',
    redoc_url: null,
    openapi_url: '/openapi.json',
    openapiVersion: '3.1',
    generateOperationIds: true,
    raiseUnknownParameters: false,
    passthroughErrors: true,
    schema: {
      info: {
        title: 'Feel8 Asset Cloud API',
        version: '1.0.0',
        description: [
          'OpenAPI contract for audited Feel8 Asset Cloud endpoints.',
          'The public application contract lives under `/v1/*` and is intended to be exercised from `/docs`.',
          'Better Auth `/api/auth/*` routes remain implementation details and are intentionally excluded from this document for now.',
        ].join('\n\n'),
      },
      tags: [
        {
          name: 'system',
          description: 'Worker capability and site-level configuration endpoints.',
        },
        {
          name: 'account',
          description: 'Authenticated user session endpoints owned by the application contract.',
        },
        {
          name: 'search',
          description: 'Cross-asset catalog search endpoints.',
        },
        {
          name: 'components',
          description: 'Component catalog, versioning, subscriptions, and fork operations.',
        },
        {
          name: 'variants',
          description: 'Variant catalog, versioning, subscriptions, and fork operations.',
        },
        {
          name: 'management',
          description: 'Administrative user, asset, and site-management endpoints.',
        },
      ],
    },
  });

  registerRoute(openapi, 'get', '/v1/auth/providers', {
    tags: ['system'],
    summary: 'Get enabled sign-in providers',
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(authProvidersResponseSchema, 'Available sign-in providers'),
    }),
  }, handlers.getAuthProviders);

  registerRoute(openapi, 'get', '/v1/site-settings', {
    tags: ['system'],
    summary: 'Get site settings',
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(siteSettingsResponseSchema, 'Current site settings'),
    }),
  }, handlers.getSiteSettings);

  registerRoute(openapi, 'get', '/v1/me', {
    tags: ['account'],
    summary: 'Get the current authenticated user',
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(apiUserResponseSchema, 'Authenticated user profile'),
    }),
  }, handlers.getMe);

  registerRoute(openapi, 'get', '/v1/components', {
    tags: ['components'],
    summary: 'List components',
    description: 'Lists component summaries visible to the current viewer.',
    request: {
      query: componentListQuerySchema,
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentPageResponseSchema, 'Component page'),
    }),
  }, handlers.listComponents);

  registerRoute(openapi, 'post', '/v1/components', {
    tags: ['components'],
    summary: 'Create a component',
    request: {
      body: jsonRequestBody(componentCreateRequestSchema, 'Component create payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentDetailSchema, 'Created component'),
    }),
  }, handlers.createComponent);

  registerRoute(openapi, 'get', '/v1/components/:componentId', {
    tags: ['components'],
    summary: 'Get a component',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentDetailSchema, 'Component detail'),
    }),
  }, handlers.routeComponentAssetRequest);

  registerRoute(openapi, 'put', '/v1/components/:componentId', {
    tags: ['components'],
    summary: 'Update a component',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
      body: jsonRequestBody(componentUpdateRequestSchema, 'Component update payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentDetailSchema, 'Updated component'),
    }),
  }, handlers.routeComponentAssetRequest);

  registerRoute(openapi, 'delete', '/v1/components/:componentId', {
    tags: ['components'],
    summary: 'Delete a component',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(emptyObjectSchema, 'Component deleted'),
    }),
  }, handlers.routeComponentAssetRequest);

  registerRoute(openapi, 'get', '/v1/components/:componentId/content', {
    tags: ['components'],
    summary: 'Get component content',
    description: 'Returns the latest published component session payload.',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentContentResponseSchema, 'Component content payload'),
    }),
  }, handlers.getComponentContent);

  registerRoute(openapi, 'put', '/v1/components/:componentId/visibility', {
    tags: ['components'],
    summary: 'Update component visibility',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
      body: jsonRequestBody(visibilityUpdateRequestSchema, 'Visibility update payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentDetailSchema, 'Component with updated visibility'),
    }),
  }, handlers.routeComponentAssetRequest);

  registerRoute(openapi, 'patch', '/v1/components/:componentId/meta', {
    tags: ['components'],
    summary: 'Update component metadata',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
      body: jsonRequestBody(assetMetaUpdateRequestSchema, 'Component metadata update payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentDetailSchema, 'Component with updated metadata'),
    }),
  }, handlers.routeComponentAssetRequest);

  registerRoute(openapi, 'get', '/v1/components/:componentId/versions', {
    tags: ['components'],
    summary: 'List component versions',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
      query: versionListQuerySchema,
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentVersionPageResponseSchema, 'Component version page'),
    }),
  }, handlers.routeComponentAssetRequest);

  registerRoute(openapi, 'get', '/v1/components/:componentId/versions/:versionNumber', {
    tags: ['components'],
    summary: 'Get a component version',
    request: {
      params: z.object({
        componentId: z.string(),
        versionNumber: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentDetailSchema, 'Component version detail'),
    }),
  }, handlers.routeComponentAssetRequest);

  registerRoute(openapi, 'get', '/v1/components/:componentId/versions/:versionNumber/content', {
    tags: ['components'],
    summary: 'Get component version content',
    request: {
      params: z.object({
        componentId: z.string(),
        versionNumber: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentContentResponseSchema, 'Component version content'),
    }),
  }, handlers.routeComponentAssetRequest);

  registerRoute(openapi, 'post', '/v1/components/:componentId/subscribe', {
    tags: ['components'],
    summary: 'Subscribe to a component',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentDetailSchema, 'Subscribed component detail'),
    }),
  }, handlers.routeComponentAssetRequest);

  registerRoute(openapi, 'delete', '/v1/components/:componentId/subscribe', {
    tags: ['components'],
    summary: 'Unsubscribe from a component',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentDetailSchema, 'Unsubscribed component detail'),
    }),
  }, handlers.routeComponentAssetRequest);

  registerRoute(openapi, 'post', '/v1/components/:componentId/fork', {
    tags: ['components'],
    summary: 'Fork a component',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
      body: jsonRequestBody(componentForkRequestSchema, 'Component fork payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(componentDetailSchema, 'Forked component'),
    }),
  }, handlers.routeComponentAssetRequest);

  registerRoute(openapi, 'get', '/v1/variants', {
    tags: ['variants'],
    summary: 'List variants',
    request: {
      query: variantListQuerySchema,
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantPageResponseSchema, 'Variant page'),
    }),
  }, handlers.listVariants);

  registerRoute(openapi, 'post', '/v1/variants', {
    tags: ['variants'],
    summary: 'Create a variant',
    request: {
      body: jsonRequestBody(variantCreateRequestSchema, 'Variant create payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantDetailSchema, 'Created variant'),
    }),
  }, handlers.createVariant);

  registerRoute(openapi, 'get', '/v1/variants/:variantId', {
    tags: ['variants'],
    summary: 'Get a variant',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantDetailSchema, 'Variant detail'),
    }),
  }, handlers.routeVariantAssetRequest);

  registerRoute(openapi, 'put', '/v1/variants/:variantId', {
    tags: ['variants'],
    summary: 'Update a variant',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
      body: jsonRequestBody(variantUpdateRequestSchema, 'Variant update payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantDetailSchema, 'Updated variant'),
    }),
  }, handlers.routeVariantAssetRequest);

  registerRoute(openapi, 'delete', '/v1/variants/:variantId', {
    tags: ['variants'],
    summary: 'Delete a variant',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(emptyObjectSchema, 'Variant deleted'),
    }),
  }, handlers.routeVariantAssetRequest);

  registerRoute(openapi, 'get', '/v1/variants/:variantId/content', {
    tags: ['variants'],
    summary: 'Get variant content',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantContentResponseSchema, 'Variant content payload'),
    }),
  }, handlers.routeVariantAssetRequest);

  registerRoute(openapi, 'put', '/v1/variants/:variantId/visibility', {
    tags: ['variants'],
    summary: 'Update variant visibility',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
      body: jsonRequestBody(visibilityUpdateRequestSchema, 'Visibility update payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantDetailSchema, 'Variant with updated visibility'),
    }),
  }, handlers.routeVariantAssetRequest);

  registerRoute(openapi, 'patch', '/v1/variants/:variantId/meta', {
    tags: ['variants'],
    summary: 'Update variant metadata',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
      body: jsonRequestBody(assetMetaUpdateRequestSchema, 'Variant metadata update payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantDetailSchema, 'Variant with updated metadata'),
    }),
  }, handlers.routeVariantAssetRequest);

  registerRoute(openapi, 'get', '/v1/variants/:variantId/versions', {
    tags: ['variants'],
    summary: 'List variant versions',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
      query: versionListQuerySchema,
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantVersionPageResponseSchema, 'Variant version page'),
    }),
  }, handlers.routeVariantAssetRequest);

  registerRoute(openapi, 'get', '/v1/variants/:variantId/versions/:versionNumber', {
    tags: ['variants'],
    summary: 'Get a variant version',
    request: {
      params: z.object({
        variantId: z.string(),
        versionNumber: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantDetailSchema, 'Variant version detail'),
    }),
  }, handlers.routeVariantAssetRequest);

  registerRoute(openapi, 'get', '/v1/variants/:variantId/versions/:versionNumber/content', {
    tags: ['variants'],
    summary: 'Get variant version content',
    request: {
      params: z.object({
        variantId: z.string(),
        versionNumber: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantContentResponseSchema, 'Variant version content'),
    }),
  }, handlers.routeVariantAssetRequest);

  registerRoute(openapi, 'post', '/v1/variants/:variantId/subscribe', {
    tags: ['variants'],
    summary: 'Subscribe to a variant',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantDetailSchema, 'Subscribed variant detail'),
    }),
  }, handlers.routeVariantAssetRequest);

  registerRoute(openapi, 'delete', '/v1/variants/:variantId/subscribe', {
    tags: ['variants'],
    summary: 'Unsubscribe from a variant',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantDetailSchema, 'Unsubscribed variant detail'),
    }),
  }, handlers.routeVariantAssetRequest);

  registerRoute(openapi, 'post', '/v1/variants/:variantId/fork', {
    tags: ['variants'],
    summary: 'Fork a variant',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
      body: jsonRequestBody(variantForkRequestSchema, 'Variant fork payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(variantDetailSchema, 'Forked variant'),
    }),
  }, handlers.routeVariantAssetRequest);

  registerRoute(openapi, 'get', '/v1/management/users', {
    tags: ['management'],
    summary: 'List users for management',
    request: {
      query: userListQuerySchema,
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(managementUserPageResponseSchema, 'Managed user page'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'post', '/v1/management/users', {
    tags: ['management'],
    summary: 'Create a managed user',
    request: {
      body: jsonRequestBody(managementUserCreateRequestSchema, 'Managed user create payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(managementUserResponseSchema, 'Created managed user'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'get', '/v1/management/users/:userId', {
    tags: ['management'],
    summary: 'Get a managed user',
    request: {
      params: z.object({
        userId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(managementUserResponseSchema, 'Managed user detail'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'put', '/v1/management/users/:userId', {
    tags: ['management'],
    summary: 'Update a managed user',
    request: {
      params: z.object({
        userId: z.string(),
      }),
      body: jsonRequestBody(managementUserUpdateRequestSchema, 'Managed user update payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(managementUserResponseSchema, 'Updated managed user'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'delete', '/v1/management/users/:userId', {
    tags: ['management'],
    summary: 'Delete a managed user',
    request: {
      params: z.object({
        userId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(emptyObjectSchema, 'Managed user deleted'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'get', '/v1/management/site-settings', {
    tags: ['management'],
    summary: 'Get site settings for management',
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(siteSettingsResponseSchema, 'Current site settings'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'put', '/v1/management/site-settings', {
    tags: ['management'],
    summary: 'Update site settings',
    request: {
      body: jsonRequestBody(siteSettingsUpdateRequestSchema, 'Site settings update payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(siteSettingsResponseSchema, 'Updated site settings'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'post', '/v1/management/assets/purge-all', {
    tags: ['management'],
    summary: 'Permanently delete every asset and revision',
    request: {
      body: jsonRequestBody(
        managementPurgeAllAssetsRequestSchema,
        'Destructive confirmation payload required to permanently delete all assets',
      ),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(managementPurgeAllAssetsResponseSchema, 'Asset purge summary'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'get', '/v1/management/components', {
    tags: ['management'],
    summary: 'List managed components',
    request: {
      query: managementComponentListQuerySchema,
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(managementAssetPageResponseSchema, 'Managed component page'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'get', '/v1/management/components/:componentId', {
    tags: ['management'],
    summary: 'Get a managed component',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
      query: managementAssetDetailQuerySchema,
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(managementAssetDetailResponseSchema, 'Managed component detail'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'put', '/v1/management/components/:componentId', {
    tags: ['management'],
    summary: 'Update a managed component',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
      body: jsonRequestBody(managementAssetUpdateRequestSchema, 'Managed asset update payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(managementAssetDetailResponseSchema, 'Updated managed component'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'delete', '/v1/management/components/:componentId', {
    tags: ['management'],
    summary: 'Delete a managed component',
    request: {
      params: z.object({
        componentId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(emptyObjectSchema, 'Managed component deleted'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'get', '/v1/management/variants', {
    tags: ['management'],
    summary: 'List managed variants',
    request: {
      query: managementVariantListQuerySchema,
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(managementAssetPageResponseSchema, 'Managed variant page'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'get', '/v1/management/variants/:variantId', {
    tags: ['management'],
    summary: 'Get a managed variant',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
      query: managementAssetDetailQuerySchema,
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(managementAssetDetailResponseSchema, 'Managed variant detail'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'put', '/v1/management/variants/:variantId', {
    tags: ['management'],
    summary: 'Update a managed variant',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
      body: jsonRequestBody(managementAssetUpdateRequestSchema, 'Managed asset update payload'),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(managementAssetDetailResponseSchema, 'Updated managed variant'),
    }),
  }, handlers.routeManagementRequest);

  registerRoute(openapi, 'delete', '/v1/management/variants/:variantId', {
    tags: ['management'],
    summary: 'Delete a managed variant',
    request: {
      params: z.object({
        variantId: z.string(),
      }),
    },
    responses: withCommonErrorResponses({
      200: jsonSuccessResponse(emptyObjectSchema, 'Managed variant deleted'),
    }),
  }, handlers.routeManagementRequest);

  return openapi;
}
